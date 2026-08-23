"""Flow search over the persisted graph (spec §7). Stdlib plus pydantic only."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from fnmatch import fnmatch
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.cache import EdgeRow, GraphCache, QueueRow
from auditor.graph.model import DEFAULT_FLOW_LIMIT, TEST_ROLES, EdgeKind

DEFAULT_HUB_FAN_IN = 40  # keep in step with GraphConfig.flow_hub_fan_in

_DISPATCH = "dispatches_to"
_DETERMINISTIC = "deterministic"
_BASE_KINDS = frozenset({EdgeKind.CALLS.value, EdgeKind.CALLBACK_ARG.value})
_OVERRIDES = frozenset({EdgeKind.OVERRIDES.value})
_REGISTERED = frozenset({EdgeKind.REGISTERED_IN.value})
_LEAF_EDGES = frozenset({EdgeKind.REGISTERED_IN.value})
# which label survives when one child is reachable by several relations, strongest first
_EDGE_PRECEDENCE = (
    EdgeKind.CALLS.value,
    _DISPATCH,
    EdgeKind.CALLBACK_ARG.value,
    EdgeKind.REGISTERED_IN.value,
)


class FlowDirection(StrEnum):
    OUT = "out"
    IN = "in"


class FlowOptions(BaseModel):
    """The knobs of one flow query, shared by the traversal, the query API and both surfaces."""

    model_config = ConfigDict(frozen=True)

    direction: FlowDirection = FlowDirection.OUT
    depth: int = Field(default=4, ge=0)
    limit: int = Field(default=DEFAULT_FLOW_LIMIT, ge=1)
    kinds: tuple[str, ...] = ()
    include_tests: bool = False
    expand_hubs: bool = False
    stop_at: tuple[str, ...] = ()
    hub_fan_in: int = Field(default=DEFAULT_HUB_FAN_IN, ge=1)


DEFAULT_OPTIONS = FlowOptions()  # frozen, so one shared instance is a safe default


class UnresolvedLeaf(BaseModel):
    """One ``graph_unresolved`` row hanging off the node that could not place the name."""

    model_config = ConfigDict(frozen=True)

    name: str
    fact_kind: str
    reason: str
    external: bool = False


class HubMark(BaseModel):
    """Why a node's fan crossed the floor, and whether that actually stopped the walk."""

    model_config = ConfigDict(frozen=True)

    count: int
    kind: Literal["fan_in", "expansion"]
    collapsed: bool = False


class _NodeMarks(BaseModel):
    """What the walk decides about one node, in one place, so ``_Record`` and ``FlowNode``
    cannot drift apart."""

    model_config = ConfigDict(frozen=True)

    id: str
    edge: str | None = None
    source: str = _DETERMINISTIC
    depth: int = 0
    seen_ref: bool = False
    cycle: bool = False
    stopped: bool = False
    hub: HubMark | None = None


class FlowNode(_NodeMarks):
    """One node of a flow tree. ``edge`` is the relation that reached it, ``None`` at the root."""

    kind: str
    children: tuple["FlowNode", ...] = ()
    unresolved: tuple[UnresolvedLeaf, ...] = ()


class FlowResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: FlowNode
    direction: FlowDirection
    modules: tuple[str, ...] = ()
    truncated: bool = False
    limit: int = DEFAULT_FLOW_LIMIT

    def node_ids(self) -> list[str]:
        """Every node the tree shows, once, in tree pre-order, so a queue read is scoped to them."""
        out: list[str] = []
        seen: set[str] = set()
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.id not in seen:
                seen.add(node.id)
                out.append(node.id)
            stack.extend(reversed(node.children))
        return out

    def with_unresolved(self, rows: Mapping[str, list[QueueRow]]) -> "FlowResult":
        """A copy with each node's queue rows hung off it, keyed by node id.

        Separate from the walk because the rows worth reading are only known once the walk has
        said which nodes it reached.
        """
        return self.model_copy(update={"root": _attach(self.root, rows)})


def _attach(node: FlowNode, rows: Mapping[str, list[QueueRow]]) -> FlowNode:
    return node.model_copy(
        update={
            "children": tuple(_attach(child, rows) for child in node.children),
            "unresolved": tuple(
                UnresolvedLeaf(
                    name=row["name"],
                    fact_kind=row["fact_kind"],
                    reason=row["reason"],
                    external=bool(row["externally_bound"]),
                )
                for row in rows.get(node.id, ())
            ),
        }
    )


class FlowPayload(FlowResult):
    """``GraphQuery.flow``'s wire shape: a ``FlowResult`` plus how the symbol resolved."""

    symbol: str
    resolved: str
    ambiguous: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        result: FlowResult,
        *,
        symbol: str,
        resolved: str,
        ambiguous: tuple[str, ...] = (),
    ) -> "FlowPayload":
        """Widen a walk result, so a field added to ``FlowResult`` reaches the wire without a
        second edit at the query seam."""
        return cls(
            symbol=symbol,
            resolved=resolved,
            ambiguous=ambiguous,
            **{name: getattr(result, name) for name in FlowResult.model_fields},
        )


class _Record(_NodeMarks):
    """One scratch row of the walk, assembled into a ``FlowNode`` at the end.

    Deliberately mutable: ``children`` grows as the walk emits them and ``hub`` is restamped when
    the hub rule refuses to expand the node.
    """

    model_config = ConfigDict(frozen=False)

    parent: int | None = None
    children: list[int] = Field(default_factory=list)


def _source(edge: EdgeRow) -> str:
    """Edge provenance. A row read before the column existed degrades to deterministic."""
    return edge.get("provenance") or _DETERMINISTIC


def _edge_rank(edge: str) -> tuple[int, str]:
    return (
        _EDGE_PRECEDENCE.index(edge)
        if edge in _EDGE_PRECEDENCE
        else len(_EDGE_PRECEDENCE),
        edge,
    )


def _dedupe(triples: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """One row per child id, the strongest relation surviving, ordered by ``(edge, child_id)``.

    Alphabetical order would let ``callback_arg`` mask a direct ``calls`` to the same child.
    """
    best: dict[str, tuple[str, str, str]] = {}
    for triple in sorted(set(triples)):
        current = best.get(triple[0])
        if current is None or _edge_rank(triple[1]) < _edge_rank(current[1]):
            best[triple[0]] = triple
    return sorted(best.values(), key=lambda t: (t[1], t[0]))


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
    """Distinct production symbols pointing at the node over the followed kinds, plus its
    dispatch children.

    Direction independent on the incoming side, which is what makes a widely called helper a hub
    in an outward tree even though it expands to almost nothing. Test callers never count, so
    ``--include-tests`` can only add children, never collapse a node that was open without it.
    """
    incoming = {
        e["src"]
        for e in cache.incoming(node_id, kinds)
        if cache.role(e["src"]) not in TEST_ROLES
    }
    dispatch = {
        child
        for child, edge, _ in _children(
            cache,
            node_id,
            direction=options.direction,
            kinds=frozenset(),
            include_tests=False,
        )
        if edge == _DISPATCH
    }
    return len(incoming | dispatch)


def _hub(
    cache: GraphCache, node_id: str, *, kinds: frozenset[str], options: FlowOptions
) -> HubMark | None:
    """The node's fan when it crosses ``hub_fan_in``, else ``None`` (spec §7).

    Both fans count production symbols only, whatever ``--include-tests`` says, so the mark is a
    property of the node rather than of the query. ``collapsed`` stays false here: whether the
    walk acted on the mark is decided in ``build_flow``.
    """
    fan_in = _fan_in(cache, node_id, kinds=kinds, options=options)
    if fan_in >= options.hub_fan_in:
        return HubMark(count=fan_in, kind="fan_in")
    expansion = len(
        _children(
            cache,
            node_id,
            direction=options.direction,
            kinds=kinds,
            include_tests=False,
        )
    )
    if expansion >= options.hub_fan_in:
        return HubMark(count=expansion, kind="expansion")
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
    return _Record(
        id=node_id,
        parent=parent,
        edge=edge,
        source=source,
        depth=depth,
        seen_ref=seen_ref,
        cycle=cycle,
        stopped=_stopped(cache.module(node_id), options.stop_at),
        hub=_hub(cache, node_id, kinds=kinds, options=options),
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
) -> FlowResult:
    """Walk the graph from ``start_id`` breadth-first and return the tree (spec §7).

    ``options.limit`` caps emitted children, the root being free; hitting it sets ``truncated``
    and leaves the shallower levels complete.
    """
    followed = _BASE_KINDS | frozenset(options.kinds)
    records = [_new_record(cache, start_id, kinds=followed, options=options)]
    modules = [cache.module(start_id)]
    module_seen = {modules[0]}
    seen = {start_id}
    truncated = False
    frontier = [0]

    for level in range(1, options.depth + 1):
        nxt: list[int] = []
        for parent in frontier:
            if truncated:
                break
            record = records[parent]
            if record.stopped:
                continue
            if parent != 0 and record.hub is not None and not options.expand_hubs:
                record.hub = record.hub.model_copy(update={"collapsed": True})
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
        """Both models extend ``_NodeMarks``, so the spread carries every mark the walk set."""
        record = records[index]
        return FlowNode(
            **record.model_dump(exclude={"parent", "children"}),
            kind=cache.kind(record.id),
            children=tuple(assemble(child) for child in record.children),
        )

    return FlowResult(
        root=assemble(0),
        direction=options.direction,
        modules=tuple(modules),
        truncated=truncated,
        limit=options.limit,
    )
