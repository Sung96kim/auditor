"""Resolve a node set's local facts into structural GraphEdges and the facts it could not
place (spec §5.6). Needs pydantic; no other third-party import."""

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.model import (
    FUNCTION_KINDS,
    TEST_ROLES,
    CallForm,
    EdgeKind,
    FactKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    Resolution,
    StructuralResult,
    UnresolvedReason,
    UnresolvedRow,
)

_EDGE_KIND_BY_FACT = {
    FactKind.CALLEE: EdgeKind.CALLS,
    FactKind.ATTR_CALLEE: EdgeKind.CALLS,
    FactKind.TYPED_CALL: EdgeKind.CALLS,
    FactKind.CLASS_REF: EdgeKind.REFERENCES_TYPE,
}
_CALL_FACTS = (FactKind.CALLEE, FactKind.ATTR_CALLEE)
_SELF_RECEIVERS = ("self", "cls")
# most tractable first: a bare call is answerable from one file, an attribute call is not
_FORM_PREFERENCE = (CallForm.BARE, CallForm.SELF, CallForm.ATTR)


def _short_name(node_id: str) -> str:
    """The bare symbol name inside a node id: ``do_thing`` for ``svc/foo.py::Foo.do_thing``."""
    return node_id.split("::")[-1].rsplit(".", 1)[-1]


def _edged_names(edges: list[GraphEdge]) -> dict[tuple[str, str], set[str]]:
    """(src, edge kind) -> the dst short names already leaving that node."""
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in edges:
        out[(e.src, e.kind.value)].add(_short_name(e.dst))
    return out


def call_forms(node: GraphNode) -> dict[tuple[str, CallForm], tuple[str | None, ...]]:
    """(name, call form) -> every receiver root the node called that name on. A name called both
    bare and on a receiver keeps an entry per form; `self` needs a direct `self`/`cls` receiver,
    so a chained `self.a.b.m()` is an attribute call."""
    out: dict[tuple[str, CallForm], list[str | None]] = {}
    for name in node.bare_callees:
        out.setdefault((name, CallForm.BARE), [None])
    for root, method, direct in node.attr_callees:
        form = CallForm.SELF if direct and root in _SELF_RECEIVERS else CallForm.ATTR
        roots = out.setdefault((method, form), [])
        if root not in roots:
            roots.append(root)
    return {key: tuple(roots) for key, roots in out.items()}


def form_for(
    forms: dict[tuple[str, CallForm], tuple[str | None, ...]],
    name: str,
    local_names: tuple[str, ...],
) -> tuple[CallForm, tuple[str | None, ...]]:
    """The form a row records for ``name``: the most tractable form it was called in that the
    node does not itself bind, so `handle()` beside `job.handle()` reports the bare call unless
    `handle` is a parameter, in which case the attribute call is the miss worth reporting."""
    for form in _FORM_PREFERENCE:
        if (name, form) in forms and not (
            form is CallForm.BARE and name in local_names
        ):
            return form, forms[(name, form)]
    return CallForm.BARE, (None,)


class _NotedFact(BaseModel):
    """One fact a pass could not place, with every receiver root it was called on: which root
    survives is only known after the whole pass has settled the non-repo receivers."""

    model_config = ConfigDict(frozen=True)

    node: GraphNode
    resolution: Resolution
    fact_kind: FactKind
    name: str
    receiver_roots: tuple[str | None, ...]
    call_form: CallForm


class NameBindings(BaseModel):
    """Which module each name in a module was imported from, and whether that source is in the repo.

    The rule the queue uses to dim a row and the rule the verifier uses to reject a proposal are the
    same rule, so they are the same object.
    """

    model_config = ConfigDict(frozen=True)

    bindings_by_module: dict[str, dict[str, str]] = Field(default_factory=dict)
    aliases_by_module: dict[str, dict[str, str]] = Field(default_factory=dict)
    dotted_to_id: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def of(
        cls,
        modules: Iterable[GraphNode],
        *,
        module_ids: Iterable[str] | None = None,
    ) -> "NameBindings":
        """Build the three indexes from the module nodes; nothing else carries an import.

        ``module_ids`` is every module in the graph, which is what decides whether an import source
        is in the repo. It defaults to ``modules``' own ids, which is right for a resolver holding
        the whole node set and wrong for a verifier holding two files.
        """
        mods = [m for m in modules if m.kind is NodeKind.MODULE]
        ids = (
            sorted(module_ids) if module_ids is not None else sorted(m.id for m in mods)
        )
        dotted: dict[str, str] = {}
        for mid in ids:
            stem = mid.removesuffix(".py").removesuffix("/__init__")
            dotted[stem.replace("/", ".")] = mid
        return cls(
            bindings_by_module={m.id: dict(m.import_bindings) for m in mods},
            aliases_by_module={m.id: dict(m.external_aliases) for m in mods},
            dotted_to_id=dotted,
        )

    def is_repo_source(self, module_id: str, src: str) -> bool:
        """Whether an import source names a repo module: by its full dotted path, or as a sibling
        of the caller's own package (`from _common import x` inside ``plugin/hooks/``)."""
        if src in self.dotted_to_id:
            return True
        parent = module_id.rsplit("/", 1)[0]
        if parent == module_id:
            return False
        return f"{parent.replace('/', '.')}.{src}" in self.dotted_to_id

    def externally_bound(self, module_id: str, *names: str | None) -> bool:
        """Whether the caller's module binds any of ``names`` from a non-repo import (``re``,
        ``subprocess``), directly or through a module-level alias (``_RX = re.compile(...)``)."""
        binds = self.bindings_by_module.get(module_id, {})
        aliases = self.aliases_by_module.get(module_id, {})
        return any(
            (src := binds.get(aliases.get(n, n))) is not None
            and not self.is_repo_source(module_id, src)
            for n in names
            if n is not None
        )


class UnresolvedCollector(BaseModel):
    """Collects the facts a resolver pass could not place and applies the post-pass gates: a
    receiver a known non-repo type already settled, and a row the node already has an edge for."""

    bindings: NameBindings
    noted: list[_NotedFact] = Field(default_factory=list)
    settled: set[tuple[str, str | None, str]] = Field(default_factory=set)

    def note(
        self,
        node: GraphNode,
        res: Resolution,
        *,
        fact_kind: FactKind,
        name: str,
        receiver_roots: tuple[str | None, ...],
        call_form: CallForm,
    ) -> None:
        """Queue one unplaced fact, unless the caller is test code, the name has no role-filtered
        repo definer, or a bare name is one of the node's own bindings."""
        if node.role in TEST_ROLES or res.reason is None or not res.definers:
            return
        if call_form is CallForm.BARE and name in node.local_names:
            return
        self.noted.append(
            _NotedFact(
                node=node,
                resolution=res,
                fact_kind=fact_kind,
                name=name,
                receiver_roots=receiver_roots,
                call_form=call_form,
            )
        )

    def settle(self, node_id: str, receiver_root: str, method: str) -> None:
        """Record that ``receiver_root``'s declared type is known and is not a repo class, so any
        call of ``method`` on that receiver is answered."""
        self.settled.add((node_id, receiver_root, method))

    def _row(self, fact: _NotedFact) -> UnresolvedRow | None:
        """The row a noted fact earns, on the first receiver root no non-repo type settled, or
        ``None`` when every root it was called on is settled."""
        roots = fact.receiver_roots
        if fact.fact_kind in _CALL_FACTS:
            roots = tuple(
                r for r in roots if (fact.node.id, r, fact.name) not in self.settled
            )
            if not roots:
                return None
        root = roots[0] if roots else None
        return UnresolvedRow(
            node_id=fact.node.id,
            fact_kind=fact.fact_kind,
            name=fact.name,
            reason=fact.resolution.reason,
            receiver_root=root,
            call_form=fact.call_form,
            candidates=fact.resolution.gated,
            definers=fact.resolution.definers,
            resolution_path=fact.resolution.path,
            externally_bound=self.bindings.externally_bound(
                fact.node.module, fact.name, root
            ),
        )

    def drain(self, edges: list[GraphEdge]) -> list[UnresolvedRow]:
        """The surviving rows, one per ``(node_id, name, reason)``: settled receivers dropped,
        names the node already has an edge of that kind to dropped, typed rows winning a tie."""
        edged = _edged_names(edges)
        kept: dict[tuple[str, str, UnresolvedReason], UnresolvedRow] = {}
        for fact in self.noted:
            row = self._row(fact)
            if row is None:
                continue
            kind = _EDGE_KIND_BY_FACT[row.fact_kind]
            if row.name in edged.get((row.node_id, kind.value), frozenset()):
                continue
            key = (row.node_id, row.name, row.reason)
            # the typed row wins a tie: it names the receiver's class, not the local variable
            if key not in kept or row.fact_kind is FactKind.TYPED_CALL:
                kept[key] = row
        return list(kept.values())


class StructuralResolver:
    """Resolves a node set's local facts into structural GraphEdges. Holds the derived
    indexes + edge accumulator as fields so each edge-type pass is its own method; the facts no
    pass could place go to an :class:`UnresolvedCollector` it owns."""

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
        self.bindings = NameBindings.of(self.modules.values())
        # module_id -> set of repo module_ids it imports (resolved from its import targets).
        # Used to disambiguate cross-module name resolution by real import evidence.
        self.imports_by_module: dict[str, set[str]] = {}
        for mid, mod in self.modules.items():
            targets = {
                dst
                for t in mod.imports
                if (dst := self.bindings.dotted_to_id.get(t)) is not None
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
        self.collector = UnresolvedCollector(bindings=self.bindings)
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
                if local == "*"
                and (dst := self.bindings.dotted_to_id.get(src)) is not None
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
                    frontier += self._resolve_name(bn, cls, self.by_class_name).ids
        return None

    def _binding_target(self, module_id: str, name: str) -> str | None:
        """The repo module id that ``module_id`` binds ``name`` from (``from a import Name`` → the
        id of module ``a``), or ``None`` if there's no such explicit binding or it's external."""
        src = self.bindings.bindings_by_module.get(module_id, {}).get(name)
        return None if src is None else self.bindings.dotted_to_id.get(src)

    def _namespace_defs(
        self,
        module_id: str,
        name: str,
        definers: set[str],
        seen: set[str],
        walked: list[str],
    ) -> set[str]:
        """Which of ``definers`` ``module_id`` re-exports ``name`` from: its own def, a star
        re-export (any module), or a named re-export in a package ``__init__`` (never a plain
        module's named import). Appends each module visited to ``walked``, in visit order."""
        if module_id in seen:
            return set()
        seen.add(module_id)
        walked.append(module_id)
        if module_id in definers:
            return {module_id}  # a local definition shadows any re-export
        out: set[str] = set()
        for star_src in self.star_reexports.get(module_id, ()):
            out |= self._namespace_defs(star_src, name, definers, seen, walked)
        if (
            module_id.endswith("/__init__.py")
            and (tgt := self._binding_target(module_id, name)) is not None
        ):
            out |= self._namespace_defs(tgt, name, definers, seen, walked)
        return out

    def _resolve_name(
        self, name: str, caller: GraphNode, index: dict[str, list[str]]
    ) -> Resolution:
        hits = index.get(name, [])
        if caller.role not in TEST_ROLES:
            hits = [h for h in hits if self.role_by_id.get(h) not in TEST_ROLES]
        definers = tuple(hits)
        same = tuple(h for h in hits if h.split("::")[0] == caller.module)
        if same:
            return Resolution(ids=same, gated=same, definers=definers)
        path: tuple[str, ...] = ()
        # Pin the name through its import source's namespace, so a named `__init__` hop still
        # reaches the definer and same-named siblings don't make `len(gated) != 1` → drop (Finding B).
        if (src_mod := self._binding_target(caller.module, name)) is not None:
            walked: list[str] = []
            exported = self._namespace_defs(
                src_mod, name, {h.split("::")[0] for h in hits}, set(), walked
            )
            path = tuple(walked)
            bound = tuple(h for h in hits if h.split("::")[0] in exported)
            if len(bound) == 1:
                return Resolution(ids=bound, gated=bound, definers=definers, path=path)
        # Cross-module: a call site gives us only the name (`x.get()` → "get"), not the receiver
        # type, so a name defined elsewhere can't be attributed by name alone — that's what made
        # every `.get()`/`from_orm()` link to a same-named repo method (false hairball). Use the
        # import graph as the disambiguator: link only to a candidate whose module the caller
        # actually imports, and only when that's unambiguous.
        imported = self.imports_by_module.get(caller.module, frozenset())
        gated = tuple(h for h in hits if h.split("::")[0] in imported)
        if len(gated) == 1:
            return Resolution(ids=gated, gated=gated, definers=definers, path=path)
        reason = (
            UnresolvedReason.AMBIGUOUS_NAME
            if len(gated) >= 2
            else UnresolvedReason.UNIMPORTABLE_NAME
        )
        return Resolution(gated=gated, definers=definers, path=path, reason=reason)

    def _chain_in_repo(self, cls_id: str, seen: set[str]) -> bool:
        """Whether ``cls_id`` and every base above it is a repo class. This is the typed-call
        gate: it drops `str.lower`, `Path.mkdir` and every pydantic receiver."""
        if cls_id in seen:
            return True
        seen.add(cls_id)
        cls = self.classes.get(cls_id)
        if cls is None:
            return False
        for base in cls.bases:
            ids = self._resolve_name(base, cls, self.by_class_name).ids
            if not ids or not all(self._chain_in_repo(b, seen) for b in ids):
                return False
        return True

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
                dst = self.bindings.dotted_to_id.get(target)
                if dst is not None and dst != mid:
                    self._add(mid, dst, EdgeKind.IMPORTS)

    def _call_edges(self) -> None:
        for n in self.fns.values():
            forms = call_forms(n)
            for callee in n.callees:
                res = self._resolve_name(callee, n, self.by_fn_name)
                for dst in res.ids:
                    self._add(n.id, dst, EdgeKind.CALLS)
                form, roots = form_for(forms, callee, n.local_names)
                self.collector.note(
                    n,
                    res,
                    fact_kind=(
                        FactKind.CALLEE
                        if form is CallForm.BARE
                        else FactKind.ATTR_CALLEE
                    ),
                    name=callee,
                    receiver_roots=roots,
                    call_form=form,
                )
            # typed-receiver calls (Finding 2): `recv.method()` where recv has a declared type
            # resolves to THAT class's method (up the inheritance chain), disambiguating
            # same-named methods that the receiver-blind name+import gate above drops.
            for recv_var, recv_type, method in n.typed_calls:
                cls_ids = self._resolve_name(recv_type, n, self.by_class_name).ids
                edged = False
                for cls_id in cls_ids:
                    if (mid := self._resolve_method(cls_id, method)) is not None:
                        self._add(n.id, mid, EdgeKind.CALLS)
                        edged = True
                # a `self.x()` miss is fully described by its self row
                if (method, CallForm.SELF) in forms:
                    continue
                if edged:
                    continue
                if self._typed_call_is_in_repo(cls_ids):
                    self.collector.note(
                        n,
                        self._resolve_name(method, n, self.by_fn_name),
                        fact_kind=FactKind.TYPED_CALL,
                        name=method,
                        receiver_roots=(recv_type,),
                        call_form=CallForm.ATTR,
                    )
                elif cls_ids or not self.by_class_name.get(recv_type):
                    # the receiver's class is settled and is not a repo class: so is the call
                    self.collector.settle(n.id, recv_var, method)
            for cb in n.callback_names:
                for dst in self._resolve_name(cb, n, self.by_fn_name).ids:
                    self._add(n.id, dst, EdgeKind.CALLBACK_ARG)
            for t in n.param_types:
                res = self._resolve_name(t, n, self.by_class_name)
                for dst in res.ids:
                    self._add(n.id, dst, EdgeKind.REFERENCES_TYPE)
                self.collector.note(
                    n,
                    res,
                    fact_kind=FactKind.CLASS_REF,
                    name=t,
                    receiver_roots=(None,),
                    call_form=CallForm.BARE,
                )
            # body class-as-value uses (Finding 3): a class instantiated/attr-accessed/passed
            # in the body edges to it, same as an annotation would. Same class-name gate, so a
            # body name resolving to a function (already a `calls` edge) never lands here.
            for ref in n.class_refs:
                res = self._resolve_name(ref, n, self.by_class_name)
                for dst in res.ids:
                    self._add(n.id, dst, EdgeKind.REFERENCES_TYPE)
                self.collector.note(
                    n,
                    res,
                    fact_kind=FactKind.CLASS_REF,
                    name=ref,
                    receiver_roots=(None,),
                    call_form=CallForm.BARE,
                )

    def _typed_call_is_in_repo(self, cls_ids: tuple[str, ...]) -> bool:
        """The typed-call gate: at least one receiver class, every one of them fully in-repo."""
        return bool(cls_ids) and all(self._chain_in_repo(c, set()) for c in cls_ids)

    def _registered_in(self) -> None:
        for sym in sorted(self.nodes, key=lambda s: s.id):
            if not sym.registry_roots:
                continue
            binds = self.bindings.bindings_by_module.get(sym.module, {})
            for root in sym.registry_roots:
                dotted = binds.get(root)
                if dotted is None:
                    continue
                dst = self.bindings.dotted_to_id.get(dotted)
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
                for b in self._resolve_name(bn, c, self.by_class_name).ids
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

    def resolve(self) -> StructuralResult:
        self._module_contains()
        self._imports()
        self._call_edges()
        self._registered_in()
        self._class_edges()
        return StructuralResult(
            edges=self.edges, unresolved=self.collector.drain(self.edges)
        )


def resolve_structural(nodes: list[GraphNode]) -> StructuralResult:
    return StructuralResolver(nodes).resolve()
