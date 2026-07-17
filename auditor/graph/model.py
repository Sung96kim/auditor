"""Pure data model for the semantic graph — stdlib only (no numpy/sklearn)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
        tuple[str, str], ...
    ] = ()  # (receiver_type, method) for calls on an annotated receiver / self
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
