"""Serve a rendered report on an ephemeral localhost port and open it in a browser.

Deliberately local-only (binds ``127.0.0.1``): an audit report can contain source
snippets, so it is never published to an external host. The page is held in memory and
served to any GET; the server runs until interrupted (Ctrl-C).
"""

import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


class _ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = self.server.payload  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", f"{self.server.content_type}; charset=utf-8")  # type: ignore[attr-defined]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            webbrowser.open(self.url)
        try:
            self.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.server_close()
