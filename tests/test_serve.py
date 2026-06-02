"""serve.py — the in-memory localhost report server."""

import threading
from urllib.request import urlopen

from auditor.serve import ReportServer, _wsl_browser_command


def test_serve_returns_document_on_localhost():
    server = ReportServer("<h1>hello</h1>")
    assert server.url.startswith("http://127.0.0.1:")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(server.url) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
            assert resp.read().decode() == "<h1>hello</h1>"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_serve_binds_loopback_only():
    # never exposes the report beyond the local machine (reports may carry source).
    server = ReportServer("x")
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_wsl_browser_prefers_first_available_launcher():
    # WSL has no Linux browser; pick a Windows launcher in priority order.
    cmd = _wsl_browser_command(
        "http://x/", which=lambda n: f"/win/{n}" if n == "explorer.exe" else None
    )
    assert cmd == ["/win/explorer.exe", "http://x/"]


def test_wsl_browser_command_none_when_no_launcher():
    assert _wsl_browser_command("http://x/", which=lambda _: None) is None


def test_wsl_browser_threads_start_args_through():
    cmd = _wsl_browser_command(
        "http://x/", which=lambda n: n if n == "cmd.exe" else None
    )
    assert cmd == ["cmd.exe", "/c", "start", "", "http://x/"]
