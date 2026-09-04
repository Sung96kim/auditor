"""The id namespace identity rows live in (spec 5.2).

One repo identity can hold several partitions, so stored ids are relative to the checkout's
toplevel. A partition applies what falls under its own prefix and leaves the rest alone. The two
readers below are the only place a node id is taken apart.
"""


def to_partition(node_id: str, prefix: str) -> str | None:
    """A stored node id as this partition sees it, or ``None`` when it is out of scope here."""
    if not prefix:
        return node_id
    return node_id[len(prefix) :] if node_id.startswith(prefix) else None


def to_toplevel(value: str, prefix: str) -> str:
    """A partition-local node id or file path in the toplevel-relative form identity rows store.

    A partition prefix is a path prefix, so an anchor's ``path`` takes it the same way its
    ``node_id`` does.
    """
    return f"{prefix}{value}"


def in_scope(node_id: str, prefix: str) -> bool:
    return to_partition(node_id, prefix) is not None


def scope_path(scope: str) -> str:
    """One run's scope as a repo-relative path prefix, with any trailing separator dropped.

    ``.`` and ``''`` both mean the whole repo, and a leading ``./`` is dropped: no node id starts
    with one, so ``./auditor`` would match nothing, brief nothing and refuse every proposal the
    run made, which is what shell completion produces.

    Raises ``ValueError`` for a scope that could never name a node here: node ids are relative to
    the checkout, so an absolute path or one climbing out of it refuses every proposal instead.
    """
    cleaned = scope.strip().rstrip("/")
    if not cleaned or cleaned == ".":
        return ""
    cleaned = cleaned.removeprefix("./")
    if cleaned.startswith("/") or ".." in cleaned.split("/"):
        raise ValueError(f"scope {scope!r} is not a repo-relative path")
    return cleaned


def under_scope(node_id: str, scope: str) -> bool:
    """Whether a node id falls under a run's scope, on a path or a symbol boundary.

    A bare prefix match puts ``svc_other.py::f`` under the scope ``svc``, and everything in
    ``auditor/graphql/`` under ``auditor/graph``.
    """
    if not scope:
        return True
    return node_id == scope or node_id.startswith((f"{scope}/", f"{scope}::"))


def short_name(node_id: str) -> str:
    """The bare symbol name inside a node id: ``g`` for ``b.py::Klass.g``."""
    return node_id.split("::")[-1].rsplit(".", 1)[-1]


def file_of(node_id: str) -> str:
    """The file a node id names: the path half for a symbol, the whole id for a module."""
    return node_id.split("::")[0]
