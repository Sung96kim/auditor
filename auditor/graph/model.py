"""Pure data model for the semantic graph — stdlib only (no numpy/sklearn)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    kind: EdgeKind
    weight: float = 1.0


class GraphCluster(BaseModel):
    model_config = ConfigDict(frozen=True)

    cluster_id: int
    label: str
    member_count: int


class FileGraphFacts(BaseModel):
    """The cached per-file extraction result (serialized into ``graph_facts``)."""

    model_config = ConfigDict(frozen=True)

    path: str
    role: str
    nodes: list[GraphNode] = []


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
    BARE = "bare"
    SELF = "self"
    ATTR = "attr"


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
    priority: int = 4
    externally_bound: bool = False


class StructuralResult(BaseModel):
    """One resolver pass: the deterministic edges it produced and the facts it could not place."""

    model_config = ConfigDict(frozen=True)

    edges: list[GraphEdge] = Field(default_factory=list)
    unresolved: list[UnresolvedRow] = Field(default_factory=list)
