"""The wire models ``GraphQuery`` returns, shared by the CLI renderers and the MCP tools.

They live beside the query rather than under ``auditor/cli`` so both surfaces read the same shape
and neither imports the other.
"""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ConfigDict, Field

from auditor.graph.model import QUEUE_ID_CAP, GraphCluster, UnresolvedRow
from auditor.payload import WirePayload, WireRows


class GraphBuildReport(WirePayload):
    """What one build landed, as ``graph build`` and the MCP tool report it."""

    nodes: int
    edges: int
    clusters: int
    unresolved: int
    findings: int
    refined: int
    expired: int


class RelatedRow(WirePayload):
    """One semantic neighbour of a symbol, with the edge weight that found it."""

    id: str
    kind: str
    weight: float
    rank: float


class RelatedReport(WireRows[RelatedRow]):
    """``graph related``."""


class NeighborRow(WirePayload):
    """One structural neighbour, with the relation and the hop count that reached it."""

    id: str
    kind: str
    edge: str
    direction: Literal["in", "out"]
    hops: int


class NeighborsReport(WireRows[NeighborRow]):
    """``graph neighbors``."""


class SearchRow(WirePayload):
    """One symbol whose id contains the search term."""

    id: str
    kind: str
    rank: float


class SearchReport(WireRows[SearchRow]):
    """``graph search``."""


class ClustersReport(WireRows[GraphCluster]):
    """``graph clusters``, over the cluster record the build already writes."""


class ClusterMember(WirePayload):
    """One node inside a concept cluster."""

    id: str
    name: str
    module: str
    rank: float
    refined: int = 0
    annotation: str | None = None


class CappedConcept(WirePayload):
    """A concept with its member list truncated and the true total alongside."""

    cluster_id: int
    label: str
    member_count: int
    members: tuple[ClusterMember, ...] = ()
    shown: int = 0


class ConceptPayload(WirePayload):
    """``graph concept``: the cluster a term resolved to, and every member it holds."""

    cluster_id: int
    label: str
    members: tuple[ClusterMember, ...] = ()

    def capped(self, limit: int) -> CappedConcept:
        """The first ``limit`` members with the true total alongside, for a bounded response.

        A negative limit is floored at zero: slicing from the end would answer a nonsense
        request with a plausible-looking page of every member but the last.
        """
        members = self.members[: max(0, limit)]
        return CappedConcept(
            cluster_id=self.cluster_id,
            label=self.label,
            member_count=len(self.members),
            members=members,
            shown=len(members),
        )


class UsageGroup(WirePayload):
    """One edge kind's usage count and a rank-ordered sample of the symbols on the other end."""

    count: int
    sample: tuple[str, ...] = ()


class UsagesPayload(WirePayload):
    """``graph usages``: structural edges grouped by kind, split by direction."""

    symbol: str
    resolved: str
    kind: str | None = None
    ambiguous: tuple[str, ...] = ()
    used_by: dict[str, UsageGroup] = Field(default_factory=dict)
    depends_on: dict[str, UsageGroup] = Field(default_factory=dict)
    total_in: int = 0
    total_out: int = 0


class QueueRowPayload(UnresolvedRow):
    """One queue row on the wire: the two id lists capped, their true totals alongside.

    ``extra="forbid"``: the queue is read with ``SELECT *``, so a column the table gains has to be
    declared here or fail loudly, never be dropped on the way to the CLI and the MCP tool.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    definers_count: int = 0
    candidates_count: int = 0

    @property
    def display_name(self) -> str:
        """The called name as written: ``job.handle`` for an attribute call, so two rows on the
        same method under different receivers are told apart."""
        return f"{self.receiver_root}.{self.name}" if self.receiver_root else self.name

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> "QueueRowPayload":
        """Cap a stored queue row's two id lists the way ``graph_overview`` caps its hub lists:
        a node can have dozens of definers."""
        return cls.model_validate(
            {
                **row,
                "definers": tuple(row["definers"])[:QUEUE_ID_CAP],
                "candidates": tuple(row["candidates"])[:QUEUE_ID_CAP],
                "definers_count": len(row["definers"]),
                "candidates_count": len(row["candidates"]),
            }
        )


class QueueReport(WireRows[QueueRowPayload]):
    """``graph unresolved``."""
