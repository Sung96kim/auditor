"""Flow search over the persisted graph (spec §7). Stdlib plus pydantic only.

``GraphCache`` is loaded once per query and shared by ``build_flow`` and ``GraphQuery.neighbors``.
The one-shot load pays for itself from roughly five visited nodes up; below that it is a wash with
per-node ``GraphDB.edges_of`` round trips.
"""

from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.model import TEST_ROLES, EdgeKind

if TYPE_CHECKING:
    from auditor.database import IndexStore

DEFAULT_HUB_FAN_IN = 40  # keep in step with GraphConfig.flow_hub_fan_in
DEFAULT_FLOW_LIMIT = 200

NodeRow = dict[str, Any]  # one graph_nodes row, as GraphDB.nodes() returns it
EdgeRow = dict[str, Any]  # one graph_edges row: src, dst, kind, weight
QueueRow = dict[
    str, Any
]  # one graph_unresolved payload row, as GraphDB.unresolved() returns it

_DISPATCH = "dispatches_to"
_DETERMINISTIC = "deterministic"
_BASE_KINDS = frozenset({EdgeKind.CALLS.value, EdgeKind.CALLBACK_ARG.value})
_OVERRIDES = frozenset({EdgeKind.OVERRIDES.value})
_REGISTERED = frozenset({EdgeKind.REGISTERED_IN.value})
_LEAF_EDGES = frozenset({EdgeKind.REGISTERED_IN.value})


class FlowDirection(StrEnum):
    OUT = "out"
    IN = "in"


def resolve_ids(node_ids: Collection[str], symbol: str) -> list[str]:
    """Node ids matching ``symbol``: the exact id, else every ``::name`` / ``.name`` suffix.

    Shared by ``GraphQuery._resolve_all`` and ``GraphQuery.flow`` so a bare name resolves the same
    way whether the ids come from the database or from a loaded cache.
    """
    if symbol in node_ids:
        return [symbol]
    return sorted(
        nid
        for nid in node_ids
        if nid.endswith(f"::{symbol}") or nid.endswith(f".{symbol}")
    )


class GraphCache:
    """Every node and edge of one repo partition, indexed by ``src`` and by ``dst``."""

    def __init__(self, nodes: list[NodeRow], edges: list[EdgeRow]) -> None:
        self.nodes: dict[str, NodeRow] = {n["node_id"]: n for n in nodes}
        self.out: dict[str, list[EdgeRow]] = {}
        self.inc: dict[str, list[EdgeRow]] = {}
        for e in edges:
            self.out.setdefault(e["src"], []).append(e)
            self.inc.setdefault(e["dst"], []).append(e)

    @classmethod
    async def load(cls, index: "IndexStore") -> "GraphCache":
        return cls(await index.graph.nodes(), await index.graph.all_edges())

    def kind(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["kind"] if node else "?"

    def module(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["module"] if node else node_id.split("::")[0]

    def role(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["role"] if node else "production"

    def rank(self, node_id: str) -> float:
        node = self.nodes.get(node_id)
        return (node.get("rank") or 0.0) if node else 0.0

    def outgoing(self, node_id: str, kinds: frozenset[str]) -> list[EdgeRow]:
        return [e for e in self.out.get(node_id, ()) if e["kind"] in kinds]

    def incoming(self, node_id: str, kinds: frozenset[str]) -> list[EdgeRow]:
        return [e for e in self.inc.get(node_id, ()) if e["kind"] in kinds]

    def incident(self, node_id: str, kinds: frozenset[str]) -> list[EdgeRow]:
        """Both directions, each edge once: a self-loop is in ``out`` and ``inc`` alike."""
        return self.outgoing(node_id, kinds) + [
            e for e in self.incoming(node_id, kinds) if e["src"] != node_id
        ]


class FlowOptions(BaseModel):
    """The knobs of one flow query, shared by the traversal, the query API and both surfaces."""

    model_config = ConfigDict(frozen=True)

    direction: FlowDirection = FlowDirection.OUT
    depth: int = 4
    limit: int = DEFAULT_FLOW_LIMIT
    kinds: tuple[str, ...] = ()
    include_tests: bool = False
    expand_hubs: bool = False
    stop_at: tuple[str, ...] = ()
    hub_fan_in: int = DEFAULT_HUB_FAN_IN


DEFAULT_OPTIONS = FlowOptions()  # frozen, so one shared instance is a safe default


class UnresolvedLeaf(BaseModel):
    """One ``graph_unresolved`` row hanging off the node that could not place the name."""

    model_config = ConfigDict(frozen=True)

    name: str
    fact_kind: str
    reason: str
    external: bool = False


class FlowNode(BaseModel):  # auditor: skip: PY-OOP-FLAT-FIELD-MODEL  (§7 tree contract)
    """One node of a flow tree. ``edge`` is the relation that reached it, ``None`` at the root."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    edge: str | None = None
    source: str = _DETERMINISTIC
    depth: int = 0
    children: tuple["FlowNode", ...] = ()
    seen_ref: bool = False
    cycle: bool = False
    stopped: bool = False
    hub: int | None = None
    hub_kind: Literal["fan_in", "expansion"] | None = None
    unresolved: tuple[UnresolvedLeaf, ...] = ()


class FlowResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: FlowNode
    direction: FlowDirection
    modules: tuple[str, ...] = ()
    truncated: bool = False
    limit: int = DEFAULT_FLOW_LIMIT


class _Record(BaseModel):  # auditor: skip: PY-OOP-FLAT-FIELD-MODEL  (mirrors FlowNode)
    """One scratch row of the walk, assembled into a ``FlowNode`` at the end.

    ``children`` holds record indices and is the only slot that grows after construction; every
    other field is decided when the node is emitted.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    parent: int | None = None
    edge: str | None = None
    source: str = _DETERMINISTIC
    depth: int = 0
    seen_ref: bool = False
    cycle: bool = False
    stopped: bool = False
    hub: int | None = None
    hub_kind: Literal["fan_in", "expansion"] | None = None
    children: list[int] = Field(default_factory=list)


def _source(edge: EdgeRow) -> str:
    """Edge provenance. S4 adds the column; until then every edge is deterministic."""
    return edge.get("source") or _DETERMINISTIC


def _dedupe(triples: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """One row per child id, ordered by ``(edge, child_id)``; the first edge label wins."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for triple in sorted(set(triples), key=lambda t: (t[1], t[0])):
        if triple[0] not in seen:
            seen.add(triple[0])
            out.append(triple)
    return out


def _children(
    cache: GraphCache,
    node_id: str,
    *,
    direction: FlowDirection,
    kinds: frozenset[str],
    include_tests: bool,
) -> list[tuple[str, str, str]]:
    """``(child_id, edge, source)`` one hop from ``node_id``, deduped and ordered.

    Outward adds each overrider as ``dispatches_to`` and each registry as a leaf; inward walks to
    the base method and expands a registry module's registrants (spec §7).
    """
    if direction is FlowDirection.OUT:
        triples = [
            (e["dst"], e["kind"], _source(e)) for e in cache.outgoing(node_id, kinds)
        ]
        triples += [
            (e["src"], _DISPATCH, _source(e))
            for e in cache.incoming(node_id, _OVERRIDES)
        ]
        triples += [
            (e["dst"], EdgeKind.REGISTERED_IN.value, _source(e))
            for e in cache.outgoing(node_id, _REGISTERED)
        ]
    else:
        triples = [
            (e["src"], e["kind"], _source(e)) for e in cache.incoming(node_id, kinds)
        ]
        triples += [
            (e["dst"], _DISPATCH, _source(e))
            for e in cache.outgoing(node_id, _OVERRIDES)
        ]
        # only a registry module has incoming registered_in edges, so no kind check is needed
        triples += [
            (e["src"], _DISPATCH, _source(e))
            for e in cache.incoming(node_id, _REGISTERED)
        ]
    if not include_tests:
        triples = [t for t in triples if cache.role(t[0]) not in TEST_ROLES]
    return _dedupe(triples)


def _stopped(module: str, globs: Sequence[str]) -> bool:
    return any(fnmatch(module, glob) for glob in globs)


def _fan_in(
    cache: GraphCache, node_id: str, *, kinds: frozenset[str], options: FlowOptions
) -> int:
    """Distinct symbols pointing at the node over the followed kinds, plus its dispatch children.

    Direction independent on the incoming side, which is what makes a widely called helper a hub
    in an outward tree even though it expands to almost nothing.
    """
    incoming = {
        e["src"]
        for e in cache.incoming(node_id, kinds)
        if options.include_tests or cache.role(e["src"]) not in TEST_ROLES
    }
    dispatch = {
        child
        for child, edge, _ in _children(
            cache,
            node_id,
            direction=options.direction,
            kinds=frozenset(),
            include_tests=options.include_tests,
        )
        if edge == _DISPATCH
    }
    return len(incoming | dispatch)


def _hub(
    cache: GraphCache, node_id: str, *, kinds: frozenset[str], options: FlowOptions
) -> tuple[int, Literal["fan_in", "expansion"]] | None:
    """``(count, which)`` when the node crosses ``hub_fan_in``, else ``None`` (spec §7)."""
    fan_in = _fan_in(cache, node_id, kinds=kinds, options=options)
    if fan_in >= options.hub_fan_in:
        return fan_in, "fan_in"
    expansion = len(
        _children(
            cache,
            node_id,
            direction=options.direction,
            kinds=kinds,
            include_tests=options.include_tests,
        )
    )
    if expansion >= options.hub_fan_in:
        return expansion, "expansion"
    return None


def _new_record(
    cache: GraphCache,
    node_id: str,
    *,
    kinds: frozenset[str],
    options: FlowOptions,
    parent: int | None = None,
    edge: str | None = None,
    source: str = _DETERMINISTIC,
    depth: int = 0,
    seen_ref: bool = False,
    cycle: bool = False,
) -> _Record:
    """A record whose marks are already decided: ``stopped`` and the hub fan describe the node
    itself, so every emitted node carries them whether or not the walk expanded it."""
    hub = _hub(cache, node_id, kinds=kinds, options=options)
    return _Record(
        id=node_id,
        parent=parent,
        edge=edge,
        source=source,
        depth=depth,
        seen_ref=seen_ref,
        cycle=cycle,
        stopped=_stopped(cache.module(node_id), options.stop_at),
        hub=None if hub is None else hub[0],
        hub_kind=None if hub is None else hub[1],
    )


def _ancestors(records: list[_Record], index: int) -> set[str]:
    out: set[str] = set()
    cursor: int | None = index
    while cursor is not None:
        out.add(records[cursor].id)
        cursor = records[cursor].parent
    return out


def build_flow(
    cache: GraphCache,
    start_id: str,
    *,
    options: FlowOptions = DEFAULT_OPTIONS,
    unresolved_by_node: Mapping[str, list[QueueRow]] | None = None,
) -> FlowResult:
    """Walk the graph from ``start_id`` breadth-first and return the tree (spec §7).

    ``options.limit`` caps emitted children, the root being free; hitting it sets ``truncated``
    and leaves the shallower levels complete.
    """
    followed = _BASE_KINDS | frozenset(options.kinds)
    rows = unresolved_by_node or {}
    records = [_new_record(cache, start_id, kinds=followed, options=options)]
    modules = [cache.module(start_id)]
    module_seen = {modules[0]}
    seen = {start_id}
    truncated = False
    frontier = [0]

    for level in range(1, options.depth + 1):
        nxt: list[int] = []
        for parent in frontier:
            record = records[parent]
            if record.stopped:
                continue
            if parent != 0 and record.hub is not None and not options.expand_hubs:
                continue  # the root always expands, however wide its fan
            pairs = _children(
                cache,
                record.id,
                direction=options.direction,
                kinds=followed,
                include_tests=options.include_tests,
            )
            ancestors = _ancestors(records, parent)
            for child_id, edge, source in pairs:
                if len(records) - 1 >= options.limit:
                    truncated = True
                    break
                cycle = child_id in ancestors
                index = len(records)
                records.append(
                    _new_record(
                        cache,
                        child_id,
                        kinds=followed,
                        options=options,
                        parent=parent,
                        edge=edge,
                        source=source,
                        depth=level,
                        seen_ref=not cycle and child_id in seen,
                        cycle=cycle,
                    )
                )
                records[parent].children.append(index)
                seen.add(child_id)
                module = cache.module(child_id)
                if module not in module_seen:
                    module_seen.add(module)
                    modules.append(module)
                child = records[index]
                if not child.seen_ref and not cycle and edge not in _LEAF_EDGES:
                    nxt.append(index)
        if truncated or not nxt:
            break
        frontier = nxt

    def assemble(index: int) -> FlowNode:
        """``_Record`` mirrors ``FlowNode`` field for field, minus the walk's own bookkeeping."""
        record = records[index]
        return FlowNode(
            **record.model_dump(exclude={"parent", "children"}),
            kind=cache.kind(record.id),
            children=tuple(assemble(child) for child in record.children),
            unresolved=tuple(
                UnresolvedLeaf(
                    name=row["name"],
                    fact_kind=row["fact_kind"],
                    reason=row["reason"],
                    external=bool(row["externally_bound"]),
                )
                for row in rows.get(record.id, ())
            ),
        )

    return FlowResult(
        root=assemble(0),
        direction=options.direction,
        modules=tuple(modules),
        truncated=truncated,
        limit=options.limit,
    )
