"""How one index handle is bound to a checkout, in a module both the path layer and the store
layer can import without importing each other."""

from pydantic import BaseModel, ConfigDict


class Partition(BaseModel):
    """The identity every worktree of one checkout shares, and the partition root's path inside
    that checkout. Identity rows key on ``identity`` and store ids prefixed by ``prefix``, so two
    partitions of one checkout never collide."""

    model_config = ConfigDict(frozen=True)

    identity: str
    prefix: str = ""
