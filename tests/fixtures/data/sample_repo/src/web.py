"""HTTP layer: typing + correctness cases, plus the route-handler edge case where a
dict[str, Any] return is exempt (a boundary), and a Jinja/Flask debug hardening case."""

import os
from typing import Any

import requests
from flask import Flask
from jinja2 import Environment

app = Flask(__name__)

_WARMUP = os.getenv("WARMUP")  # PY-CONFIG-ADHOC-ENV
# PY-CONFIG-IMPORT-TIME-IO: a network call executed at import time (module scope)
_MANIFEST = requests.get("https://example.com/manifest.json", timeout=5).json()


def build_env(loader) -> Environment:
    # PY-SEC-JINJA-AUTOESCAPE-OFF (autoescape not enabled)
    return Environment(loader=loader)


@app.get("/health")
def health() -> dict[str, Any]:
    # route handler boundary -> PY-TYPING-UNTYPED-DICT must NOT fire here
    return {"ok": True}


def serialize_widget(widget) -> dict[str, Any]:
    # non-boundary -> PY-TYPING-UNTYPED-DICT fires
    return {"id": widget.id, "name": widget.name}


def untyped(a, b):
    # PY-TYPING-MISSING-HINTS
    return a + b


def risky_parse(raw):
    # PY-CORRECT-BROAD-EXCEPT (+ PY-CORRECT-SWALLOWED-EXCEPTION)
    try:
        return int(raw)
    except Exception:
        pass


def quiet_parse(raw: str) -> int | None:
    # PY-CORRECT-SWALLOWED-EXCEPTION only (specific exception, but swallowed)
    try:
        return int(raw)
    except ValueError:
        pass
    return None


def main() -> None:
    # PY-SEC-FLASK-DEBUG (debug=True)
    app.run(host="127.0.0.1", debug=True)
