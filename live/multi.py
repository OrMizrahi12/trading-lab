"""Several runners in ONE process over ONE IB connection (one client ID). Paper trading only.

Each pair is an ordinary `Runner`: its own state file, its own reconcile, its own orders (tag `live:<strategy>:<symbol>`),
its own feed. They take turns in a single thread, once a minute, so they never act at the same time. What is shared:
the IB connection, the risk limits (per-symbol caps plus the account-wide trade count and daily loss limit, which are
read from IB), the STOP file, the alerter and the `Link` that makes every runner re-check its state after a reconnect.

One pair halting (or hitting an error) does not stop the others; the loop ends when every pair has halted or stopped.
"""

import logging
from contextlib import ExitStack

from .config import POLL_SECONDS
from .daily_log import running
from .state import StopFileError, lock

log = logging.getLogger("live")


class MultiRunner:
    def __init__(self, runners: list):
        if not runners:
            raise ValueError("no engines to run")
        symbols = [r.symbol for r in runners]
        if len(set(symbols)) != len(symbols):
            raise ValueError(f"two engines on the same symbol: {symbols}")
        if len({id(r.ib) for r in runners}) != 1:
            raise ValueError("all engines must share one IB connection")
        self.runners = runners
        self.ib = runners[0].ib

    def _live(self) -> list:
        return [r for r in self.runners if r.state is not None and not r.state.halted and not r.stopped]

    def _tick(self, runner) -> None:
        try:
            with running(runner.label):
                runner.tick()
        except Exception as e:  # tick() handles the expected failures; this is the last line of defence for the others
            log.exception("%s: unexpected error in a poll: %s", runner.label, e)
            try:
                with running(runner.label):
                    runner._halt(f"unexpected error: {type(e).__name__}: {e}")
            except Exception:
                log.exception("%s: could not even record the halt", runner.label)
                runner.stopped = True

    def run(self, once: bool = False) -> None:
        with ExitStack() as stack:
            for r in self.runners:
                stack.enter_context(lock(r.dir))
            try:
                for r in self.runners:
                    with running(r.label):
                        r.start()
                while True:
                    for r in self._live():
                        self._tick(r)
                    if once or not self._live():
                        break
                    self.runners[0]._pause(POLL_SECONDS)
            except StopFileError as e:  # the kill switch: every runner stops, none is halted
                for r in self.runners:
                    r.stopped = True
                with running(None):
                    log.error("%s", e)
                    from .console import say

                    say(f"STOP      : {e}")
            except KeyboardInterrupt:
                for r in self.runners:
                    with running(r.label):
                        r._interrupted()
