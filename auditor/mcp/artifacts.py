# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""Large tool payloads served as MCP *resources* instead of dumped inline.

A tool that would otherwise return tens of thousands of tokens (a full scan, the consolidated
AUDIT.md) instead stashes the body here and returns a small ``ResourceLink``. The agent fetches
the bytes on demand via ``resources/read`` — and resource reads bypass the tool-response size
limit, so the full artifact is always available in one piece.

The store is in-process and keyed by (kind, seed): one live entry per repo+artifact-kind, so a
re-run overwrites rather than leaks. It is intentionally ephemeral — read the link right after
you get it, within the same server session."""

import hashlib

from mcp.types import ResourceLink

from auditor.mcp.server import mcp

_SCHEME = "audit://artifact/"
_STORE: dict[str, str] = {}


def _key(kind: str, seed: str) -> str:
    # sha256 (not for security — just a stable, URI-safe token from the target path)
    digest = hashlib.sha256(f"{kind}:{seed}".encode()).hexdigest()[:16]
    return f"{kind}-{digest}"


def publish(
    kind: str,
    seed: str,
    body: str,
    *,
    mime_type: str,
    name: str,
    description: str,
) -> ResourceLink:
    """Stash ``body`` and return a ``ResourceLink`` pointing at it. ``kind`` groups artifacts
    (``scan``/``report``/``audit``); ``seed`` (a path) makes the URI stable per target."""
    key = _key(kind, seed)
    _STORE[key] = body
    return ResourceLink(
        type="resource_link",
        uri=f"{_SCHEME}{key}",
        name=name,
        mimeType=mime_type,
        description=description,
        size=len(body.encode("utf-8")),
    )


@mcp.resource(_SCHEME + "{key}", mime_type="text/plain")
def _artifact(key: str) -> str:
    """Read back an artifact published by :func:`publish`. The concrete media type is carried on
    the ``ResourceLink`` that produced this URI (markdown for AUDIT.md, JSON for a full scan)."""
    body = _STORE.get(key)
    if body is None:
        raise ValueError(
            f"no such artifact: {key!r} (it may have expired — re-run the tool that produced it)"
        )
    return body
