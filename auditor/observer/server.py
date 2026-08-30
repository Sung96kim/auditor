"""The daemon's transport: stdlib ``ThreadingHTTPServer`` on loopback, in a thread (spec 8.1)."""

import json
import logging
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import BaseModel, ConfigDict

from auditor.payload import WirePayload

MAX_BODY_BYTES = 1 << 20
#: `shutdown()` waits one poll interval, and the stdlib default of 0.5 s is paid by every test
_POLL_SECONDS = 0.05
#: the deadline on one connection, so a client that declares a body and sends none frees its thread
REQUEST_TIMEOUT = 10.0
#: the only Host values a same-machine page can send; anything else is a rebinding attempt
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_LOG = logging.getLogger("auditor.observer")


def loopback_host(raw: str | None) -> bool:
    """Whether a ``Host`` header names this machine. A missing header is not a claim, so it passes."""
    if not raw:
        return True
    name = raw.strip()
    if name.startswith("["):  # a bracketed IPv6 literal, with or without a port
        name = name.partition("]")[0] + "]"
    elif name.count(":") == 1:
        name = name.rsplit(":", 1)[0]
    return name.lower() in _LOOPBACK_HOSTS


class Reply(BaseModel):
    """One answer, already serialized, so a payload subclass cannot be coerced to its base."""

    model_config = ConfigDict(frozen=True)

    status: int = 200
    body: str = ""
    content_type: str = "application/json"
    etag: str = ""
    #: the connection cannot be reused, because the request body was never read off it
    close: bool = False

    @classmethod
    def json(
        cls, payload: WirePayload, *, status: int = 200, etag: str = ""
    ) -> "Reply":
        return cls(status=status, body=payload.model_dump_json(), etag=etag)

    @classmethod
    def html(cls, document: str, *, status: int = 200) -> "Reply":
        return cls(status=status, body=document, content_type="text/html")

    @classmethod
    def error(cls, status: int, reason: str, *, close: bool = False) -> "Reply":
        return cls(status=status, body=json.dumps({"error": reason}), close=close)


Dispatch = Callable[[str, str, Mapping[str, str], bytes], Reply]


class _Handler(BaseHTTPRequestHandler):
    """Every method funnels into one dispatch, so routing lives in ``routes.py`` alone."""

    protocol_version = "HTTP/1.1"
    #: `socketserver` reads this in `setup()` and calls `settimeout` with it
    timeout = REQUEST_TIMEOUT

    def do_GET(self) -> None:
        self._answer("GET")

    def do_HEAD(self) -> None:
        self._answer("HEAD")

    def do_POST(self) -> None:
        self._answer("POST")

    def do_PUT(self) -> None:
        self._answer("PUT")

    def do_DELETE(self) -> None:
        self._answer("DELETE")

    def _refused(self) -> Reply | None:
        """Why this request is answered without being dispatched at all, or None to go on.

        Loopback binding stops other hosts, never other origins: a `text/plain` POST is a CORS
        simple request, so a page the user visits could otherwise reach the side-effecting routes.
        """
        if "Origin" in self.headers:
            return Reply.error(403, "cross-origin requests are refused", close=True)
        if not loopback_host(self.headers.get("Host")):
            return Reply.error(403, "only a loopback Host is answered", close=True)
        if "Transfer-Encoding" in self.headers:
            # the chunks would stay in the socket to be parsed as the next request line
            return Reply.error(411, "a Content-Length is required", close=True)
        return None

    def _length(self) -> int | Reply:
        """How many body bytes were declared, or the refusal an unusable declaration earns."""
        try:
            declared = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return Reply.error(400, "unreadable Content-Length", close=True)
        if declared < 0:
            return Reply.error(400, "negative Content-Length", close=True)
        if declared > MAX_BODY_BYTES:
            # reading a prefix would leave the rest to be parsed as the next request line
            return Reply.error(413, f"body over {MAX_BODY_BYTES} bytes", close=True)
        return declared

    def _answer(self, method: str) -> None:
        refused = self._refused()
        if refused is not None:
            self._write(refused)
            return
        declared = self._length()
        if isinstance(declared, Reply):
            self._write(declared)
            return
        try:
            body = self.rfile.read(declared) if declared else b""
        except OSError:  # the `timeout` deadline, or a client that went away mid-body
            self.close_connection = True
            return
        try:
            reply = self.server.dispatch(method, self.path, self.headers, body)  # type: ignore[attr-defined]
        except Exception:
            _LOG.exception("unhandled error answering %s %s", method, self.path)
            reply = Reply.error(
                500, f"the daemon failed to answer {method} {self.path}"
            )
        self._write(reply)

    def _write(self, reply: Reply) -> None:
        """Serialize one `Reply`. The only place a socket is written (P30)."""
        payload = reply.body.encode("utf-8")
        if reply.close:
            self.close_connection = True
        try:
            self.send_response(reply.status)
            self.send_header("Content-Type", f"{reply.content_type}; charset=utf-8")
            # a 304 names a cached body and carries none of its own
            if reply.status != 304:
                self.send_header("Content-Length", str(len(payload)))
            if reply.etag:
                self.send_header("ETag", reply.etag)
            if reply.close:
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (  # auditor: skip: PY-CORRECT-SWALLOWED-EXCEPTION
            BrokenPipeError,
            ConnectionResetError,
        ):
            # the page navigated away mid-response; there is nothing to handle.
            pass

    def log_message(self, *_: object) -> None:  # the daemon's own log is the record
        pass


class ObserverServer(ThreadingHTTPServer):
    """Loopback only: an audit graph and a verbatim prompt never leave this machine."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, dispatch: Dispatch, *, port: int) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.dispatch = dispatch
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        """Serve in a background thread, so `POST /events` never waits on anyone's event loop."""
        self._thread = threading.Thread(
            target=self.serve_forever, args=(_POLL_SECONDS,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.server_close()
