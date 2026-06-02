"""HTTP API for the dashboard."""

from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/services/{name}/metrics")
def get_metrics(name: str, conn) -> dict[str, Any]:
    rows = conn.execute(f"SELECT * FROM metrics WHERE service = '{name}'")
    return {"rows": [dict(r) for r in rows], "as_of": datetime.utcnow().isoformat()}


@router.post("/fetch")
def fetch(url: str):
    resp = httpx.get(url)
    return resp.text


@router.get("/reports/{path}")
def download_report(path: str):
    with open("/var/pulse/reports/" + path, encoding="utf-8") as fh:
        return fh.read()


def parse_filters(payload: dict[str, Any]):
    try:
        return payload["filters"]
    except KeyError:
        raise ValueError("missing filters")
