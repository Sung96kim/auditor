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
        # module_id -> {local_name: source_dotted}: which module each name is imported FROM.
        # `from a import Model` binds Model to module `a`, pinning its source even when several
        # reachable modules define a same-named Model (Finding B).
        self.bindings_by_module: dict[str, dict[str, str]] = {
            mid: dict(mod.import_bindings) for mid, mod in self.modules.items()
        }
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
        # Reachability through re-export surfaces, so a symbol imported via an aggregator
        # resolves to its DEFINING module (Finding 1). Always on: the len==1 gate in
        # _resolve_name means extra reachability can only recover a true edge or drop a
        # genuinely-ambiguous one — it never invents an edge to a module the caller can't reach.
        #   • package __init__.py — one level, any import form (conventional re-export surface;
        #     snapshot `base` avoids cascading through non-__init__ imports).
        #   • star re-exports (`from X import *`) — transitive, any module: a star import puts
        #     all of X's public names into this module's namespace, a sound namespace-inclusion
        #     relation to follow to a fixpoint (a plain module's *explicit* imports are NOT
        #     followed — that would close the whole graph into a hairball).
        base = {mid: set(imps) for mid, imps in self.imports_by_module.items()}
        star_map = self.star_reexports = self._star_reexport_map()
        for imps in self.imports_by_module.values():
            for imported in tuple(imps):
                if imported.endswith("/__init__.py"):
                    imps |= base.get(imported, set())
            frontier = set(imps)
            while frontier:
                nxt = {t for x in frontier for t in star_map.get(x, ())}
                new = nxt - imps
                imps |= new
                frontier = new
        self.edges: list[GraphEdge] = []
        self._seen: set[tuple[str, str, str]] = set()

    def _add(self, src: str, dst: str, kind: EdgeKind, weight: float = 1.0) -> None:
        if src != dst and (src, dst, kind.value) not in self._seen:
            self._seen.add((src, dst, kind.value))
            self.edges.append(GraphEdge(src=src, dst=dst, kind=kind, weight=weight))

    def _star_reexport_map(self) -> dict[str, set[str]]:
        """module_id -> the repo module_ids it star-re-exports from (``from X import *``), read
        from the ``("*", source)`` import bindings and resolved through ``dotted_to_id``."""
        out: dict[str, set[str]] = {}
        for mid, mod in self.modules.items():
            targets = {
                dst
                for local, src in mod.import_bindings
                if local == "*" and (dst := self.dotted_to_id.get(src)) is not None
            }
            if targets:
                out[mid] = targets
        return out

    def _resolve_method(self, cls_id: str, method: str) -> str | None:
        """The method node ``method`` reachable from class ``cls_id`` — own class first, then up
        the resolvable inheritance chain. ``None`` if no such method node exists."""
        seen: set[str] = set()
        frontier = [cls_id]
        while frontier:
            cid = frontier.pop()
            if cid in seen:
                continue
            seen.add(cid)
            if (mid := f"{cid}.{method}") in self.fns:
                return mid
            if (cls := self.classes.get(cid)) is not None:
                for bn in cls.bases:
                    frontier += self._resolve_name(bn, cls, self.by_class_name)
        return None

    def _binding_target(self, module_id: str, name: str) -> str | None:
        """The repo module id that ``module_id`` binds ``name`` from (``from a import Name`` → the
        id of module ``a``), or ``None`` if there's no such explicit binding or it's external."""
        src = self.bindings_by_module.get(module_id, {}).get(name)
        return None if src is None else self.dotted_to_id.get(src)

    def _namespace_defs(
        self, module_id: str, definers: set[str], seen: set[str]
    ) -> set[str]:
        """Which of ``definers`` (modules that define the name) ``module_id`` actually exports the
        name from: its own definition, else followed through its STAR re-exports (`from X import
        *`) to a fixpoint. Named imports are NOT followed — `from m import WorkflowBlueprint`
        doesn't put m's *other* same-named symbols into this namespace. Scopes a re-exported
        binding (`from orion.database import ComponentBlueprint`) to the one definition the
        aggregator really exports, not every same-named class the caller can transitively reach."""
        if module_id in seen:
            return set()
        seen.add(module_id)
        if module_id in definers:
            return {module_id}  # a local definition shadows any star re-export
        out: set[str] = set()
        for star_src in self.star_reexports.get(module_id, ()):
            out |= self._namespace_defs(star_src, definers, seen)
        return out

    def _resolve_name(
        self, name: str, caller: GraphNode, index: dict[str, list[str]]
    ) -> list[str]:
        hits = index.get(name, [])
        if caller.role not in TEST_ROLES:
            hits = [h for h in hits if self.role_by_id.get(h) not in TEST_ROLES]
        same = [h for h in hits if h.split("::")[0] == caller.module]
        if same:
            return same
        # The caller's import pins the source: `from orion.database import ComponentBlueprint` means
        # the one class `orion.database` exports. Resolve through the source's namespace (own def +
        # star re-exports) so same-named siblings don't make `len(gated) > 1` → drop (Finding B).
        if (src_mod := self._binding_target(caller.module, name)) is not None:
            definers = {h.split("::")[0] for h in hits}
            exported = self._namespace_defs(src_mod, definers, set())
            bound = [h for h in hits if h.split("::")[0] in exported]
            if len(bound) == 1:
                return bound
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
            # typed-receiver calls (Finding 2): `recv.method()` where recv has a declared type
            # resolves to THAT class's method (up the inheritance chain), disambiguating
            # same-named methods that the receiver-blind name+import gate above drops.
            for recv_type, method in n.typed_calls:
                for cls_id in self._resolve_name(recv_type, n, self.by_class_name):
                    if (mid := self._resolve_method(cls_id, method)) is not None:
                        self._add(n.id, mid, EdgeKind.CALLS)
            for cb in n.callback_names:
                for dst in self._resolve_name(cb, n, self.by_fn_name):
                    self._add(n.id, dst, EdgeKind.CALLBACK_ARG)
            for t in n.param_types:
                for dst in self._resolve_name(t, n, self.by_class_name):
                    self._add(n.id, dst, EdgeKind.REFERENCES_TYPE)
            # body class-as-value uses (Finding 3): a class instantiated/attr-accessed/passed
            # in the body edges to it, same as an annotation would. Same class-name gate, so a
            # body name resolving to a function (already a `calls` edge) never lands here.
            for ref in n.class_refs:
                for dst in self._resolve_name(ref, n, self.by_class_name):
                    self._add(n.id, dst, EdgeKind.REFERENCES_TYPE)

    def _registered_in(self) -> None:
        for sym in sorted(self.nodes, key=lambda s: s.id):
            if not sym.registry_roots:
                continue
            binds = self.bindings_by_module.get(sym.module, {})
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
