"""Pure data model for the semantic graph — stdlib only (no numpy/sklearn)."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


FUNCTION_KINDS = (
    NodeKind.FUNCTION,
    NodeKind.METHOD,
)  # callable symbol kinds (single source)


class EdgeKind(StrEnum):
    CONTAINS = "contains"
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    OVERRIDES = "overrides"
    CALLBACK_ARG = "callback_arg"
    REGISTERED_IN = "registered_in"
    REFERENCES_TYPE = "references_type"
    NAME_SIMILAR = "name_similar"
    USAGE_SIMILAR = "usage_similar"


class Provenance(StrEnum):
    """Where a merged graph row came from. The detectors only ever see ``DETERMINISTIC``."""

    DETERMINISTIC = "deterministic"
    REFINED = "refined"


TEST_ROLES = ("test", "test_support")  # roles grouped as "test code" across the graph


class GraphNode(BaseModel):
    """A symbol node. `id` = ``<repo-rel-path>::<qualname>`` (methods: ``path::Class.method``).
    The unresolved fact fields (``callees``/``param_types``/``bases``/``method_names``) hold short
    names; the repo pass resolves them to node ids. ``rank``/``cluster_id``/``abstractness``/
    ``text_sparse`` are filled by the repo pass (defaults for the extraction phase)."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    name: str
    module: str
    qualname: str
    doc_tokens: tuple[str, ...] = ()
    callees: tuple[str, ...] = ()
    param_types: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()  # class nodes only: base short names
    method_names: tuple[str, ...] = ()  # class nodes only: own method names
    callback_names: tuple[str, ...] = ()  # short names this fn passes as a callback arg
    class_refs: tuple[
        str, ...
    ] = ()  # body-loaded names (class-as-value uses: Model(), Model.col, f(Model))
    typed_calls: tuple[
        tuple[str, str, str], ...
    ] = ()  # (receiver var, receiver type, method) per call on an annotated receiver / self
    attr_callees: tuple[
        tuple[str | None, str, bool], ...
    ] = ()  # (receiver root or None, method, receiver is the root itself) per attribute call
    bare_callees: tuple[str, ...] = ()  # names called as a bare `name()` call
    local_names: tuple[
        str, ...
    ] = ()  # every name the function binds: all parameter forms, nested def/class/lambda
    # names and their parameters, `except ... as` targets, local imports, body assignments
    external_aliases: tuple[
        tuple[str, str], ...
    ] = ()  # module nodes: (alias, imported root) per module-level `alias = root(...)`
    imports: tuple[str, ...] = ()  # module nodes: candidate dotted import targets
    import_bindings: tuple[
        tuple[str, str], ...
    ] = ()  # module nodes: (local_name, source_dotted)
    registry_roots: tuple[str, ...] = ()  # root names of attribute-style decorators
    semantic_profile: tuple[str, ...] = ()  # behavior attrs that hold (Høst-Østvold)
    is_hof: bool = False
    is_stub: bool = False
    line: int = 0
    role: str = "production"
    abstractness: float = 0.0
    rank: float = 0.0
    cluster_id: int | None = None
    text_sparse: bool = False
    refined: bool = False
    annotation: str | None = None


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    kind: EdgeKind
    weight: float = 1.0
    provenance: Provenance = Provenance.DETERMINISTIC
    confirmed: bool = False


class GraphCluster(BaseModel):
    model_config = ConfigDict(frozen=True)

    cluster_id: int
    label: str
    member_count: int
    label_provenance: Provenance = Provenance.DETERMINISTIC


class FileGraphFacts(BaseModel):
    """The cached per-file extraction result (serialized into ``graph_facts``)."""

    model_config = ConfigDict(frozen=True)

    path: str
    role: str
    nodes: list[GraphNode] = []


#: Fact tuples unioned on a same-id merge; identity scalars keep the first def's value. The build
#: hashes most of this list into a node's `truth_sha`, so a new field belongs here only when a
#: refinement should expire when it changes; `graph/hashes.py` names the two exceptions.
UNION_FACT_FIELDS = (
    "doc_tokens",
    "callees",
    "param_types",
    "decorators",
    "bases",
    "method_names",
    "callback_names",
    "class_refs",
    "typed_calls",
    "attr_callees",
    "bare_callees",
    "local_names",
    "imports",
    "import_bindings",
    "external_aliases",
    "registry_roots",
    "semantic_profile",
)


class UnresolvedReason(StrEnum):
    """Why a fact sits in the queue. The first two come from the resolver, the rest from the
    build pass over the clustered graph."""

    AMBIGUOUS_NAME = "ambiguous_name"
    UNIMPORTABLE_NAME = "unimportable_name"
    TEXT_SPARSE = "text_sparse"
    GENERIC_LABEL = "generic_label"
    SINGLETON_CLUSTER = "singleton_cluster"


class Resolution(BaseModel):
    """One name-resolution attempt: what it picked (``ids``), what it weighed (``gated``), every
    role-filtered repo definition of the name (``definers``), and the modules it went through."""

    model_config = ConfigDict(frozen=True)

    ids: tuple[str, ...] = ()
    gated: tuple[str, ...] = ()
    definers: tuple[str, ...] = ()
    path: tuple[str, ...] = ()
    reason: UnresolvedReason | None = None


class FactKind(StrEnum):
    """Which extracted fact a queue row is about. ``NODE`` covers the build-pass rows, whose
    subject is a symbol or a cluster rather than a name the resolver tried to place."""

    CALLEE = "callee"
    ATTR_CALLEE = "attr_callee"
    CLASS_REF = "class_ref"
    TYPED_CALL = "typed_call"
    NODE = "node"


class CallForm(StrEnum):
    """How a name was called at the site the resolver could not place: a bare ``name()``, a
    direct ``self``/``cls`` receiver, or any other attribute receiver."""

    BARE = "bare"
    SELF = "self"
    ATTR = "attr"


def row_limit(limit: int) -> int:
    """One log page size, bounded at both ends, so no caller pulls the whole table into an answer.

    Floored and capped rather than refused because this is the tool path; the CLI's ``RowLimit``
    option carries the same bounds and rejects an out-of-range value before it reaches here.
    """
    return max(1, min(limit, MAX_LOG_ROWS))


def enum_values(
    raw: Sequence[str] | None, enum: type[StrEnum], field: str
) -> list[str] | None:
    """Validate a repeatable filter against its enum, so a typo is an error rather than an empty
    page the caller reads as an empty result. Every typo is named, not just the first."""
    if not raw:
        return None
    allowed = _allowed(enum)
    unknown = [v for v in raw if v not in allowed]
    if unknown:
        raise ValueError(
            f"unknown {field}: {', '.join(unknown)}. Valid: {', '.join(allowed)}"
        )
    return list(raw)


def enum_value(raw: str, enum: type[StrEnum], field: str) -> str:
    """Validate one value against its enum, worded exactly as :func:`enum_values` words it.

    The singular half: a repeatable filter reads an empty input as "every value", and a parameter
    that takes exactly one has no such reading, so ``""`` is a typo here rather than a default.
    """
    allowed = _allowed(enum)
    if raw not in allowed:
        raise ValueError(f"unknown {field}: {raw}. Valid: {', '.join(allowed)}")
    return raw


def _allowed(enum: type[StrEnum]) -> list[str]:
    """The values one enum offers, in declaration order, for a message that names the set."""
    return [e.value for e in enum]


def unresolved_priority(reason: UnresolvedReason, call_form: CallForm) -> int:
    """Drain order for the queue (spec §8.3 item 3), lowest first. 0 is reserved for the
    ``flow_leaf`` bump a flow request applies in S3."""
    if reason is UnresolvedReason.AMBIGUOUS_NAME:
        return 1
    if reason is UnresolvedReason.UNIMPORTABLE_NAME:
        return 2 if call_form in (CallForm.SELF, CallForm.BARE) else 3
    return 4


class UnresolvedRow(BaseModel):
    """One queue row: a fact the deterministic pass could not place, carrying everything a
    refiner needs to judge it without re-deriving the resolution."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    fact_kind: FactKind
    name: str
    reason: UnresolvedReason
    receiver_root: str | None = None
    call_form: CallForm = CallForm.BARE
    candidates: tuple[str, ...] = ()
    definers: tuple[str, ...] = ()
    resolution_path: tuple[str, ...] = ()
    priority: int
    externally_bound: bool = False

    @model_validator(mode="before")
    @classmethod
    def _derive_priority(cls, data: Any) -> Any:
        """Fill ``priority`` from the reason and call form unless the caller passed one, so drain
        order can never disagree with the row it orders. S3's ``flow_leaf`` bump passes one."""
        if not isinstance(data, dict) or data.get("priority") is not None:
            return data
        try:
            reason = UnresolvedReason(data["reason"])
            call_form = CallForm(data.get("call_form") or CallForm.BARE)
        except (KeyError, ValueError):
            return data
        return {**data, "priority": unresolved_priority(reason, call_form)}

    @classmethod
    def for_node(
        cls, node_id: str, name: str, reason: UnresolvedReason
    ) -> "UnresolvedRow":
        """A build-pass row, whose subject is a symbol or a cluster rather than a name the
        resolver tried to place."""
        return cls(node_id=node_id, fact_kind=FactKind.NODE, name=name, reason=reason)


# Queue display policy, shared by every surface so the CLI and the MCP tool cannot drift apart.
QUEUE_ROW_LIMIT = 50
QUEUE_ID_CAP = 10
#: default row cap for `graph log` and `graph refinements`, so the two surfaces cannot drift
LOG_ROW_LIMIT = 50
#: hard ceiling for both, so neither an agent's context nor a terminal takes the whole table
MAX_LOG_ROWS = 500
#: how many of a batch's paths a run row carries on the wire, for the same reason as `QUEUE_ID_CAP`
LOG_FILE_CAP = 10
#: how many of them the log line names before it counts the rest; S8b's page reads the same number
LOG_NOTE_FILES = 3
# Flow walk policy, same reason. MAX_FLOW_DEPTH also bounds the four recursions over the tree.
DEFAULT_FLOW_LIMIT = 200
DEFAULT_FLOW_DEPTH = 4
MAX_FLOW_LIMIT = 1000
MAX_FLOW_DEPTH = 64


class StructuralResult(BaseModel):
    """One resolver pass: the deterministic edges it produced and the facts it could not place."""

    model_config = ConfigDict(frozen=True)

    edges: list[GraphEdge] = Field(default_factory=list)
    unresolved: list[UnresolvedRow] = Field(default_factory=list)
