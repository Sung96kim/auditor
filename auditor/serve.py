"""Serve a rendered report on an ephemeral localhost port and open it in a browser.

Deliberately local-only (binds ``127.0.0.1``): an audit report can contain source
snippets, so it is never published to an external host. The page is held in memory and
served to any GET; the server runs until interrupted (Ctrl-C).
"""

import platform
import shutil
import subprocess
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

# WSL ships no Linux browser, so Python's webbrowser/xdg-open opens nothing. These launch
# the Windows default browser instead; it reaches 127.0.0.1 via WSL2 localhost forwarding.
_WSL_LAUNCHERS: tuple[tuple[str, ...], ...] = (
    ("wslview",),
    ("explorer.exe",),
    ("cmd.exe", "/c", "start", ""),
)


def _is_wsl() -> bool:
    return "microsoft" in platform.uname().release.lower()


def _wsl_browser_command(
    url: str, *, which: Callable[[str], str | None] = shutil.which
) -> list[str] | None:
    """The first available Windows-side launcher command for ``url`` (or None)."""
    for launcher in _WSL_LAUNCHERS:
        exe = which(launcher[0])
        if exe:
            return [exe, *launcher[1:], url]
    return None


def _spawn(command: list[str]) -> bool:
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def open_url(url: str) -> bool:
    """Open ``url`` in a browser; on WSL prefer the Windows default browser. Best-effort."""
    if _is_wsl():
        command = _wsl_browser_command(url)
        if command is not None and _spawn(command):
            return True
    return webbrowser.open(url)


class _ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = self.server.payload  # type: ignore[attr-defined]
        try:
            self.send_response(200)
            self.send_header("Content-Type", f"{self.server.content_type}; charset=utf-8")  # type: ignore[attr-defined]
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):  # auditor: skip: PY-CORRECT-SWALLOWED-EXCEPTION
            # a client disconnect mid-response (reload / navigate away / cancelled a large
            # payload) is nothing to handle — swallowing it is the correct behavior.
            pass

    def log_message(self, *_: object) -> None:  # silence default stderr logging
        pass


class ReportServer(HTTPServer):
    """A localhost server that returns one in-memory document for every request."""

    def __init__(self, document: str, *, content_type: str = "text/html") -> None:
        super().__init__(("127.0.0.1", 0), _ReportHandler)
        self.payload = document.encode("utf-8")
        self.content_type = content_type

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    def serve(self, *, open_browser: bool = True) -> None:
        """Open the report in a browser and serve until interrupted."""
        if open_browser:
            open_url(self.url)
        try:
            self.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.server_close()
