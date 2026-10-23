"""A tiny read-only web page over the trade journal. Standard library only, localhost only, its own process.

`GET /` is the page, `GET /api/data` the JSON behind it. Nothing else is served, nothing can be changed through it, and
a request whose Host header is not localhost is refused (a web page on another site cannot reach it through DNS tricks).
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard import data

log = logging.getLogger("dashboard")

HOST = "127.0.0.1"
PORT = 8765
PAGE = (Path(__file__).parent / "index.html").read_bytes()
LOCAL_NAMES = {"127.0.0.1", "localhost"}


def host_is_local(header: str | None) -> bool:
    """The Host header names this machine (with or without a port)."""
    if not header:
        return False
    name = header.rsplit(":", 1)[0] if not header.endswith("]") else header
    return name.lower() in LOCAL_NAMES


class Handler(BaseHTTPRequestHandler):
    db_path: Path = data.DEFAULT_DB

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not host_is_local(self.headers.get("Host")):
            return self._send(403, b"forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/data":
            try:
                body = json.dumps(data.load(self.db_path)).encode()
            except Exception:
                log.exception("reading the journal failed")
                return self._send(500, b'{"error": "could not read the journal"}', "application/json")
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args) -> None:  # quiet: one line per request is noise
        log.debug(fmt, *args)


def make_server(db_path: Path = data.DEFAULT_DB, port: int = PORT) -> ThreadingHTTPServer:
    """A server bound to 127.0.0.1 only (never 0.0.0.0). Port 0 picks a free one."""
    handler = type("BoundHandler", (Handler,), {"db_path": Path(db_path)})
    return ThreadingHTTPServer((HOST, port), handler)
