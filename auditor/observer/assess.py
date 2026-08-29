"""Spec 8.6's change assessment: does this edit batch warrant a refinement run?

Pure functions over frozen models. Nothing here reads a file, opens a store or looks at a clock:
the loop does the I/O and hands the results in, which is what lets one rule serve the daemon, the
tests and a probe.
"""

from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from auditor.graph.hashes import FileHashes, file_hashes, node_facts_sha, node_truth_sha
from auditor.graph.model import FileGraphFacts, GraphNode


class PathOutcome(StrEnum):
    """What stage 1 found for one path: spec 8.6's three table rows and the three it names in
    prose, cheapest first."""

    UNCHANGED = "unchanged"
    UNPARSED = "unparsed"
    FACTS_ONLY = "facts_only"
    TRUTH = "truth"
    ADDED = "added"
    REMOVED = "removed"


#: the outcomes whose facts the loop must write before it rebuilds; `REMOVED` writes by deleting
_PERSISTED = frozenset(
    {PathOutcome.FACTS_ONLY, PathOutcome.TRUTH, PathOutcome.ADDED, PathOutcome.REMOVED}
)


class NodeDigest(BaseModel):
    """One node's spec 5.5 pair, so a batch can name the nodes whose facts moved (P21)."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    truth: str
    facts: str

    @classmethod
    def of(cls, node: GraphNode) -> "NodeDigest":
        """The same two functions ``file_hashes`` rolls, at the granularity the field names."""
        return cls(
            node_id=node.id, truth=node_truth_sha(node), facts=node_facts_sha(node)
        )


class CachedFile(BaseModel):
    """What ``graph_facts`` held for one path, read before anything was written (spec 8.6)."""

    model_config = ConfigDict(frozen=True)

    content_hash: str | None = None
    hashes: FileHashes | None = None
    node_hashes: tuple[NodeDigest, ...] = ()

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(d.node_id for d in self.node_hashes)


class EditedFile(BaseModel):
    """One path of a batch as the loop read it.

    ``cached`` of ``None`` is a path the index has never seen; ``content_hash`` of ``None`` is a
    path that is gone.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    cached: CachedFile | None = None
    content_hash: str | None = None
    extracted: FileGraphFacts | None = None


class PathVerdict(BaseModel):
    """Stage 1's answer for one path: what moved, and whether the loop must write and rebuild."""

    model_config = ConfigDict(frozen=True)

    path: str
    outcome: PathOutcome
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    facts_changed_nodes: tuple[str, ...] = ()

    @property
    def persist(self) -> bool:
        return self.outcome in _PERSISTED


def _ids(facts: FileGraphFacts | None) -> tuple[str, ...]:
    return () if facts is None else tuple(n.id for n in facts.nodes)


def assess_path(edited: EditedFile) -> PathVerdict:
    """Classify one edited path against the facts cached for it (spec 8.6 stage 1).

    The content hash short circuit comes first, so a Stop path set's repeated dirty file costs a
    string comparison rather than a parse.
    """
    cached, extracted = edited.cached, edited.extracted
    if edited.content_hash is None:
        return PathVerdict(
            path=edited.path,
            outcome=PathOutcome.REMOVED,
            removed_nodes=cached.node_ids if cached else (),
        )
    if cached is None:
        return PathVerdict(
            path=edited.path, outcome=PathOutcome.ADDED, added_nodes=_ids(extracted)
        )
    if cached.content_hash == edited.content_hash or extracted is None:
        return PathVerdict(path=edited.path, outcome=PathOutcome.UNCHANGED)
    # `FileExtractor.extract` swallows a SyntaxError and returns no nodes, and a file that parses
    # always has at least its module node, so this is a save caught mid-edit
    if cached.node_hashes and not extracted.nodes:
        return PathVerdict(path=edited.path, outcome=PathOutcome.UNPARSED)
    fresh = file_hashes(extracted.nodes)
    if cached.hashes == fresh:
        return PathVerdict(path=edited.path, outcome=PathOutcome.UNCHANGED)
    digests = {d.node_id: d for d in cached.node_hashes}
    now_digests = {n.id: NodeDigest.of(n) for n in extracted.nodes}
    was, now = frozenset(digests), frozenset(now_digests)
    # a half written cache pair degrades to a miss the way `GraphDB.hashes` does, and the safe
    # direction for a miss is to rebuild; equal truth rolls already imply equal id sets, because
    # `file_hashes` rolls `id:truth` pairs, so there is no id comparison to add here
    facts_only = cached.hashes is not None and cached.hashes.truth == fresh.truth
    return PathVerdict(
        path=edited.path,
        outcome=PathOutcome.FACTS_ONLY if facts_only else PathOutcome.TRUTH,
        added_nodes=tuple(sorted(now - was)),
        removed_nodes=tuple(sorted(was - now)),
        facts_changed_nodes=tuple(
            sorted(i for i in now & was if digests[i] != now_digests[i])
        ),
    )


class Stage1(BaseModel):
    """Stage 1 over a whole batch: the per path verdicts and the union of what they moved."""

    model_config = ConfigDict(frozen=True)

    verdicts: tuple[PathVerdict, ...] = ()

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(v.path for v in self.verdicts)

    @property
    def persist_paths(self) -> tuple[str, ...]:
        return tuple(v.path for v in self.verdicts if v.persist)

    @property
    def needs_rebuild(self) -> bool:
        """Whether one path moved enough to make the graph worth rebuilding (spec 8.6's table)."""
        return bool(self.persist_paths)

    @property
    def added_nodes(self) -> tuple[str, ...]:
        return self._union(lambda v: v.added_nodes)

    @property
    def removed_nodes(self) -> tuple[str, ...]:
        return self._union(lambda v: v.removed_nodes)

    @property
    def facts_changed_nodes(self) -> tuple[str, ...]:
        return self._union(lambda v: v.facts_changed_nodes)

    @property
    def changed_nodes(self) -> frozenset[str]:
        """Every node this batch touched, which is what an anchor is tested against (P10)."""
        return frozenset(
            self.added_nodes + self.removed_nodes + self.facts_changed_nodes
        )

    def _union(
        self, field: Callable[[PathVerdict], tuple[str, ...]]
    ) -> tuple[str, ...]:
        """One node set across every verdict, sorted and deduplicated, so a fourth is one line."""
        return tuple(sorted({i for v in self.verdicts for i in field(v)}))


def stage_one(edited: Sequence[EditedFile]) -> Stage1:
    """Classify a whole batch, one verdict per path, in the order the loop first listed them.

    A path listed twice is classified once, first occurrence winning: a `PostToolUse` event and
    the Stop path set name the same file, and the debounce hands both over as one batch (P23).
    """
    seen: dict[str, EditedFile] = {}
    for one in edited:
        seen.setdefault(one.path, one)
    return Stage1(verdicts=tuple(assess_path(e) for e in seen.values()))
