"""The wire models ``GraphQuery`` returns, shared by the CLI renderers and the MCP tools.

They live beside the query rather than under ``auditor/cli`` so both surfaces read the same shape
and neither imports the other.
"""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from auditor.graph.model import QUEUE_ID_CAP, UnresolvedRow


class RelatedRow(BaseModel):
    """One semantic neighbour of a symbol, with the edge weight that found it."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    weight: float
    rank: float


class RelatedReport(RootModel[tuple[RelatedRow, ...]]):
    """``graph related``."""

    model_config = ConfigDict(frozen=True)


class NeighborRow(BaseModel):
    """One structural neighbour, with the relation and the hop count that reached it."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    edge: str
    direction: Literal["in", "out"]
    hops: int


class NeighborsReport(RootModel[tuple[NeighborRow, ...]]):
    """``graph neighbors``."""

    model_config = ConfigDict(frozen=True)


class SearchRow(BaseModel):
    """One symbol whose id contains the search term."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    rank: float


class SearchReport(RootModel[tuple[SearchRow, ...]]):
    """``graph search``."""

    model_config = ConfigDict(frozen=True)


class ClusterRow(BaseModel):
    """One concept cluster, with where its label came from."""

    model_config = ConfigDict(frozen=True)

    cluster_id: int
    label: str
    member_count: int
    label_provenance: str | None = None


class ClustersReport(RootModel[tuple[ClusterRow, ...]]):
    """``graph clusters``."""

    model_config = ConfigDict(frozen=True)


class ClusterMember(BaseModel):
    """One node inside a concept cluster."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    module: str
    rank: float
    refined: int = 0
    annotation: str | None = None


class CappedConcept(BaseModel):
    """A concept with its member list truncated and the true total alongside."""

    model_config = ConfigDict(frozen=True)

    cluster_id: int
    label: str
    member_count: int
    members: tuple[ClusterMember, ...] = ()
    shown: int = 0


class ConceptPayload(BaseModel):
    """``graph concept``: the cluster a term resolved to, and every member it holds."""

    model_config = ConfigDict(frozen=True)

    cluster_id: int
    label: str
    members: tuple[ClusterMember, ...] = ()

    def capped(self, limit: int) -> CappedConcept:
        """The first ``limit`` members with the true total alongside, for a bounded response."""
        return CappedConcept(
            cluster_id=self.cluster_id,
            label=self.label,
            member_count=len(self.members),
            members=self.members[:limit],
            shown=len(self.members[:limit]),
        )


class UsageGroup(BaseModel):
    """One edge kind's usage count and a rank-ordered sample of the symbols on the other end."""

    model_config = ConfigDict(frozen=True)

    count: int
    sample: tuple[str, ...] = ()


class UsagesPayload(BaseModel):
    """``graph usages``: structural edges grouped by kind, split by direction."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    resolved: str
    kind: str | None = None
    ambiguous: tuple[str, ...] = ()
    used_by: dict[str, UsageGroup] = Field(default_factory=dict)
    depends_on: dict[str, UsageGroup] = Field(default_factory=dict)
    total_in: int = 0
    total_out: int = 0


class QueueRowPayload(UnresolvedRow):
    """One queue row on the wire: the two id lists capped, their true totals alongside."""

    definers_count: int = 0
    candidates_count: int = 0

    @property
    def display_name(self) -> str:
        """The called name as written: ``job.handle`` for an attribute call, so two rows on the
        same method under different receivers are told apart."""
        return f"{self.receiver_root}.{self.name}" if self.receiver_root else self.name

    @classmethod
    def of(cls, row: Mapping[str, Any], cap: int = QUEUE_ID_CAP) -> "QueueRowPayload":
        """Cap a stored queue row's two id lists the way ``graph_overview`` caps its hub lists:
        a node can have dozens of definers."""
        return cls.model_validate(
            {
                **row,
                "definers": tuple(row["definers"])[:cap],
                "candidates": tuple(row["candidates"])[:cap],
                "definers_count": len(row["definers"]),
                "candidates_count": len(row["candidates"]),
            }
        )


class QueueReport(RootModel[tuple[QueueRowPayload, ...]]):
    """``graph unresolved``."""

    model_config = ConfigDict(frozen=True)
