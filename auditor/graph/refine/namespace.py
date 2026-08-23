"""The id namespace identity rows live in (spec 5.2).

One repo identity can hold several partitions, so stored ids are relative to the checkout's
toplevel. A partition applies what falls under its own prefix and leaves the rest alone.
"""


def to_toplevel(node_id: str, prefix: str) -> str:
    """A partition-relative node id in the form identity rows store."""
    return f"{prefix}{node_id}"


def to_partition(node_id: str, prefix: str) -> str | None:
    """A stored node id as this partition sees it, or ``None`` when it is out of scope here."""
    if not prefix:
        return node_id
    return node_id[len(prefix) :] if node_id.startswith(prefix) else None


def in_scope(node_id: str, prefix: str) -> bool:
    return to_partition(node_id, prefix) is not None
