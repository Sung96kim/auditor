"""Resolve a node set's local facts into structural GraphEdges (spec §5). Stdlib only."""

from collections import defaultdict

from auditor.graph.model import (
    FUNCTION_KINDS,
    TEST_ROLES,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)


class StructuralResolver:
    """Resolves a node set's local facts into structural GraphEdges. Holds the derived
    indexes + edge accumulator as fields so each edge-type pass is its own method."""

    def __init__(self, nodes: list[GraphNode]) -> None:
        self.nodes = nodes
        self.fns = {n.id: n for n in nodes if n.kind in FUNCTION_KINDS}
        self.classes = {n.id: n for n in nodes if n.kind == "class"}
        self.modules = {n.id: n for n in nodes if n.kind == "module"}
        self.by_fn_name: dict[str, list[str]] = defaultdict(list)
        for n in self.fns.values():
            self.by_fn_name[n.name].append(n.id)
        self.by_class_name: dict[str, list[str]] = defaultdict(list)
        for c in self.classes.values():
            self.by_class_name[c.name].append(c.id)
        self.role_by_id = {n.id: n.role for n in nodes}
        self.dotted_to_id: dict[str, str] = {}
        for mid in sorted(self.modules):
            stem = mid.removesuffix(".py")
            if stem.endswith("/__init__"):
                stem = stem[: -len("/__init__")]
            self.dotted_to_id[stem.replace("/", ".")] = mid
        # module_id -> set of repo module_ids it imports (resolved from its import targets).
        # Used to disambiguate cross-module name resolution by real import evidence.
        self.imports_by_module: dict[str, set[str]] = {}
        for mid, mod in self.modules.items():
            targets = {
                dst
                for t in mod.imports
                if (dst := self.dotted_to_id.get(t)) is not None
            }
            self.imports_by_module[mid] = targets
        self.edges: list[GraphEdge] = []
        self._seen: set[tuple[str, str, str]] = set()

    def _add(self, src: str, dst: str, kind: EdgeKind, weight: float = 1.0) -> None:
        if src != dst and (src, dst, kind.value) not in self._seen:
            self._seen.add((src, dst, kind.value))
            self.edges.append(GraphEdge(src=src, dst=dst, kind=kind, weight=weight))

    def _resolve_name(
        self, name: str, caller: GraphNode, index: dict[str, list[str]]
    ) -> list[str]:
        hits = index.get(name, [])
        if caller.role not in TEST_ROLES:
            hits = [h for h in hits if self.role_by_id.get(h) not in TEST_ROLES]
        same = [h for h in hits if h.split("::")[0] == caller.module]
        if same:
            return same
        # Cross-module: a call site gives us only the name (`x.get()` → "get"), not the receiver
        # type, so a name defined elsewhere can't be attributed by name alone — that's what made
        # every `.get()`/`from_orm()` link to a same-named repo method (false hairball). Use the
        # import graph as the disambiguator: link only to a candidate whose module the caller
        # actually imports, and only when that's unambiguous.
        imported = self.imports_by_module.get(caller.module, frozenset())
        gated = [h for h in hits if h.split("::")[0] in imported]
        return gated if len(gated) == 1 else []

    def _module_contains(self) -> None:
        top_level = [
            n
            for n in self.nodes
            if n.kind in (*FUNCTION_KINDS, NodeKind.CLASS) and "." not in n.qualname
        ]
        for mid in sorted(self.modules):
            for sym in sorted(top_level, key=lambda s: s.id):
                if sym.module == mid:
                    self._add(mid, sym.id, EdgeKind.CONTAINS)

    def _imports(self) -> None:
        for mid in sorted(self.modules):
            for target in self.modules[mid].imports:
                dst = self.dotted_to_id.get(target)
                if dst is not None and dst != mid:
                    self._add(mid, dst, EdgeKind.IMPORTS)

    def _call_edges(self) -> None:
        for n in self.fns.values():
            for callee in n.callees:
                for dst in self._resolve_name(callee, n, self.by_fn_name):
                    self._add(n.id, dst, EdgeKind.CALLS)
            for cb in n.callback_names:
                for dst in self._resolve_name(cb, n, self.by_fn_name):
                    self._add(n.id, dst, EdgeKind.CALLBACK_ARG)
            for t in n.param_types:
                for dst in self._resolve_name(t, n, self.by_class_name):
                    self._add(n.id, dst, EdgeKind.REFERENCES_TYPE)

    def _registered_in(self) -> None:
        bindings_by_module = {
            mid: dict(self.modules[mid].import_bindings) for mid in sorted(self.modules)
        }
        for sym in sorted(self.nodes, key=lambda s: s.id):
            if not sym.registry_roots:
                continue
            binds = bindings_by_module.get(sym.module, {})
            for root in sym.registry_roots:
                dotted = binds.get(root)
                if dotted is None:
                    continue
                dst = self.dotted_to_id.get(dotted)
                if dst is not None and dst != sym.id:
                    self._add(sym.id, dst, EdgeKind.REGISTERED_IN)

    def _class_edges(self) -> None:
        for c in self.classes.values():
            prefix = f"{c.id}."
            for fid in self.fns:
                if fid.startswith(prefix):
                    self._add(c.id, fid, EdgeKind.CONTAINS)
            base_ids = [
                b
                for bn in c.bases
                for b in self._resolve_name(bn, c, self.by_class_name)
            ]
            for bid in base_ids:
                self._add(c.id, bid, EdgeKind.INHERITS)
            for mname in c.method_names:
                mid = f"{c.id}.{mname}"
                if mid not in self.fns:
                    continue
                for bid in base_ids:
                    base_method = f"{bid}.{mname}"
                    if base_method in self.fns:
                        self._add(mid, base_method, EdgeKind.OVERRIDES)

    def resolve(self) -> list[GraphEdge]:
        self._module_contains()
        self._imports()
        self._call_edges()
        self._registered_in()
        self._class_edges()
        return self.edges


def resolve_structural(nodes: list[GraphNode]) -> list[GraphEdge]:
    return StructuralResolver(nodes).resolve()
