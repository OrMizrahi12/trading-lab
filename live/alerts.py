"""Push alerts through ntfy.sh. Paper trading only.

ntfy (https://ntfy.sh) turns an HTTP POST to https://ntfy.sh/<topic> into a notification on every device or browser tab
subscribed to that topic. There is no account and no password: whoever knows the topic name can read AND post, so the
name is the secret. It is a random string kept in `.env` (gitignored) as NTFY_TOPIC, never in the code or in a commit.

Alerts are for things a person must know about, never for routine bars: a fill, a rejected order, a halt, a lost
connection, the kill switch. The text is short and carries no account number. Nothing here can stop trading: sending
runs on a background thread with a short timeout, and a failed send is only logged.

    uv run -m live.alerts --init     make a random topic and add NTFY_TOPIC to .env (never overwrites one)
    uv run -m live.alerts --test     send one test alert and print the web link
    uv run -m live.alerts --link     print the web link to watch the topic in a browser
"""

import argparse
import logging
import os
import queue
import re
import secrets
import sys
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger("live")

ENV_FILE = Path(__file__).parent.parent / ".env"
ENV_KEY = "NTFY_TOPIC"
BASE_URL = "https://ntfy.sh"
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")  # url-safe, long enough that nobody can guess it
MAX_TEXT = 300
TIMEOUT_S = 5

# kind -> (ntfy priority, ntfy tag). Tags are emoji names (ntfy shows the emoji); headers must stay plain ASCII.
KINDS = {
    "fill": ("default", "white_check_mark"),
    "reject": ("high", "x"),
    "halt": ("urgent", "rotating_light"),
    "connection": ("high", "electric_plug"),
    "kill": ("urgent", "octagonal_sign"),
    "test": ("low", "bell"),
}


def read_topic(env=None, env_file: Path = ENV_FILE) -> str | None:
    """NTFY_TOPIC from the environment, else from `.env`. Only that one line of the file is looked at."""
    env = os.environ if env is None else env
    if env.get(ENV_KEY):
        return env[ENV_KEY].strip()
    try:
        for line in Path(env_file).read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == ENV_KEY:
                return value.strip().strip("'\"") or None
    except OSError:
        pass
    return None


def _http_post(url: str, body: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # raises on a non-2xx answer
        resp.read()


class Alerter:
    """`send(kind, text)` queues one notification. With no topic it does nothing (alerts off)."""

    def __init__(self, topic: str | None, *, post=None, base: str = BASE_URL, background: bool = True):
        if topic is not None and not TOPIC_RE.fullmatch(topic):
            raise ValueError(f"{ENV_KEY} must be 16-64 letters, digits, - or _ (make one with `uv run -m live.alerts --init`)")
        self.topic, self.base = topic, base.rstrip("/")
        self._post = post or _http_post
        self._background = background
        self._keys: set[str] = set()
        self._queue: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self.failures = 0

    @classmethod
    def from_env(cls, **kw) -> "Alerter":
        """The alerter for this machine; if the topic is missing or malformed, alerts are off and the log says so."""
        try:
            alerter = cls(read_topic(), **kw)
        except ValueError as e:
            log.error("alerts off: %s", e)
            return cls(None, **kw)
        if not alerter.enabled:
            log.info("alerts off: no %s in the environment or .env", ENV_KEY)
        return alerter

    @property
    def enabled(self) -> bool:
        return self.topic is not None

    @property
    def link(self) -> str | None:
        return f"{self.base}/{self.topic}" if self.topic else None

    def send(self, kind: str, text: str, *, key: str | None = None) -> bool:
        """Queue an alert. `key` makes it once-only until `reset(key)` (e.g. one 'connection lost' per outage)."""
        if not self.enabled:
            return False
        if key is not None:
            if key in self._keys:
                return False
            self._keys.add(key)
        priority, tag = KINDS[kind]
        text = " ".join(text.split())
        text = text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 3] + "..."
        headers = {"Title": f"Trading lab (paper): {kind}", "Priority": priority, "Tags": tag}
        job = (f"{self.base}/{self.topic}", text.encode("utf-8"), headers)
        log.info("alert [%s]: %s", kind, text)
        if not self._background:
            self._deliver(*job)
            return True
        if self._thread is None:
            self._queue = queue.Queue()
            self._thread = threading.Thread(target=self._work, name="ntfy", daemon=True)
            self._thread.start()
        self._queue.put(job)
        return True

    def reset(self, key: str) -> None:
        self._keys.discard(key)

    def _deliver(self, url, body, headers) -> bool:
        for attempt in (1, 2):  # one retry for a transient failure
            try:
                self._post(url, body, headers)
                return True
            except Exception as e:
                if attempt == 2:
                    self.failures += 1
                    log.warning("alert not delivered: %s: %s", type(e).__name__, e)
        return False

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            self._deliver(*job)

    def close(self, timeout: float = 10.0) -> None:
        """Let queued alerts go out (the program is about to exit), waiting at most `timeout` seconds."""
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout)
            self._thread = None


NO_ALERTS = Alerter(None)


def init_topic(env_file: Path = ENV_FILE, env=None) -> tuple[str, bool]:
    """(topic, created). Adds NTFY_TOPIC=<random> to the env file unless one is already set; never overwrites."""
    existing = read_topic(os.environ if env is None else env, env_file)
    if existing:
        return existing, False
    topic = secrets.token_urlsafe(18)  # 24 url-safe characters
    env_file = Path(env_file)
    old = env_file.read_text() if env_file.exists() else ""
    sep = "" if not old or old.endswith("\n") else "\n"
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)  # 0600 only matters if the file is new
    with os.fdopen(fd, "a") as f:
        f.write(f"{sep}{ENV_KEY}={topic}\n")
    return topic, True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true", help="create a random topic in .env if there is none")
    group.add_argument("--test", action="store_true", help="send one test alert")
    group.add_argument("--link", action="store_true", help="print the browser link")
    args = ap.parse_args(argv)
    if args.init:
        topic, created = init_topic()
        print(("Created" if created else "Already set:") + f" {ENV_KEY} in {ENV_FILE.name} (not committed).")
        print(f"Watch it in a browser: {BASE_URL}/{topic}")
        return 0
    alerter = Alerter.from_env(background=False)
    if not alerter.enabled:
        print(f"No {ENV_KEY} yet. Run `uv run -m live.alerts --init` first.")
        return 2
    print(f"Watch it in a browser: {alerter.link}")
    if args.link:
        return 0
    alerter.send("test", "Test alert from the trading lab (paper). If you can read this, alerts work.")
    if alerter.failures:
        print("The test alert was NOT delivered (see the warning above).")
        return 1
    print("Test alert sent.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
