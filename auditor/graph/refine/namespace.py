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


def to_toplevel(node_id: str, prefix: str) -> str:
    """A partition-local node id in the toplevel-relative form the identity tables store."""
    return f"{prefix}{node_id}"


def in_scope(node_id: str, prefix: str) -> bool:
    return to_partition(node_id, prefix) is not None


def short_name(node_id: str) -> str:
    """The bare symbol name inside a node id: ``g`` for ``b.py::Klass.g``."""
    return node_id.split("::")[-1].rsplit(".", 1)[-1]


def file_of(node_id: str) -> str:
    """The file a node id names: the path half for a symbol, the whole id for a module."""
    return node_id.split("::")[0]
