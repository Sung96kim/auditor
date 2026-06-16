import ast

from auditor.languages.python.resolve import CalleeResolver, find_site_packages


def _repo(tmp_path, files: dict[str, str]):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return CalleeResolver(tmp_path)


def _call(src: str) -> tuple[ast.Call, ast.Module]:
    tree = ast.parse(src)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    return call, tree


def test_resolves_from_import_sibling(tmp_path):
    r = _repo(tmp_path, {"app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n"})
    call, tree = _call("from app.helpers import reload\nreload(s, o)\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "reload"


def test_resolves_module_attr_call(tmp_path):
    r = _repo(tmp_path, {"app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n"})
    call, tree = _call("import app.helpers\napp.helpers.reload(s, o)\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "reload"


def test_unresolvable_returns_none(tmp_path):
    r = _repo(tmp_path, {"app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n"})
    call, tree = _call(
        "from third_party.db import refresh_orms\nrefresh_orms(s, [o])\n"
    )
    assert r.resolve_func(call, tree) is None
    call2, tree2 = _call("getattr(x, 'reload')(s, o)\n")
    assert r.resolve_func(call2, tree2) is None


def test_resolves_package_init_module(tmp_path):
    # a module that is a package (app/helpers/__init__.py) resolves via the __init__ path
    r = _repo(
        tmp_path, {"app/helpers/__init__.py": "def reload(s, o):\n    s.refresh(o)\n"}
    )
    call, tree = _call("from app.helpers import reload\nreload(s, o)\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "reload"


def test_syntax_error_target_returns_none(tmp_path):
    r = _repo(tmp_path, {"app/helpers.py": "def reload(:\n"})  # unparseable
    call, tree = _call("from app.helpers import reload\nreload(s, o)\n")
    assert r.resolve_func(call, tree) is None


def test_def_not_found_returns_none(tmp_path):
    r = _repo(tmp_path, {"app/helpers.py": "def other():\n    return 1\n"})
    call, tree = _call("from app.helpers import reload\nreload(s, o)\n")
    assert r.resolve_func(call, tree) is None


def test_find_site_packages_locates_venv(tmp_path):
    sp = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
    sp.mkdir(parents=True)
    assert find_site_packages(tmp_path) == sp


def test_find_site_packages_none_when_absent(tmp_path):
    assert find_site_packages(tmp_path) is None


def _dep_repo(tmp_path, *, resolve_packages, dep_pkg, dep_src):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    sp = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / dep_pkg
    sp.mkdir(parents=True)
    (sp / "__init__.py").write_text(dep_src)
    return CalleeResolver(
        tmp_path,
        resolve_packages=tuple(resolve_packages),
        site_packages=find_site_packages(tmp_path),
    )


_RO_SRC = "def refresh_orms(s, objs):\n    for o in objs:\n        s.refresh(o)\n"


def test_resolves_in_reach_dependency(tmp_path):
    r = _dep_repo(tmp_path, resolve_packages=["atmo"], dep_pkg="atmo", dep_src=_RO_SRC)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_dependency_out_of_reach_returns_none(tmp_path):
    r = _dep_repo(tmp_path, resolve_packages=[], dep_pkg="atmo", dep_src=_RO_SRC)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


def test_in_reach_but_no_env_returns_none(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    r = CalleeResolver(tmp_path, resolve_packages=("atmo",), site_packages=None)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


def test_phase1_constructor_still_works(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "helpers.py").write_text(
        "def reload(s, o):\n    s.refresh(o)\n"
    )
    r = CalleeResolver(tmp_path)
    call, tree = _call("from app.helpers import reload\nreload(s, o)\n")
    assert r.resolve_func(call, tree) is not None


# ---------------------------------------------------------------------------
# Edge-case A: resolver-level
# ---------------------------------------------------------------------------


def _dep_repo_nested(tmp_path, *, resolve_packages, subpkg, func_src):
    """Like _dep_repo but places func_src in <site-packages>/<subpkg>/__init__.py
    where subpkg may contain '/' separators for deeper nesting."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    sp = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
    pkg_dir = sp
    for part in subpkg.split("/"):
        pkg_dir = pkg_dir / part
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(func_src)
    return CalleeResolver(
        tmp_path,
        resolve_packages=tuple(resolve_packages),
        site_packages=find_site_packages(tmp_path),
    )


def test_aliased_dep_import_resolves(tmp_path):
    """A1: `from atmo import refresh_orms as ro; ro(s, [o])` resolves to refresh_orms."""
    r = _dep_repo(tmp_path, resolve_packages=["atmo"], dep_pkg="atmo", dep_src=_RO_SRC)
    call, tree = _call("from atmo import refresh_orms as ro\nro(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_dotted_dep_submodule_resolves(tmp_path):
    """A2: dep in atmo/database/__init__.py; `from atmo.database import refresh_orms` resolves."""
    r = _dep_repo_nested(
        tmp_path,
        resolve_packages=["atmo"],
        subpkg="atmo/database",
        func_src=_RO_SRC,
    )
    call, tree = _call("from atmo.database import refresh_orms\nrefresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_attribute_dep_call_resolves(tmp_path):
    """A3: `import atmo.database as db; db.refresh_orms(s, [o])` resolves (nested dep)."""
    r = _dep_repo_nested(
        tmp_path,
        resolve_packages=["atmo"],
        subpkg="atmo/database",
        func_src=_RO_SRC,
    )
    call, tree = _call("import atmo.database as db\ndb.refresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_prefix_boundary_no_false_match(tmp_path):
    """A4: `atmosphere` present + resolve_packages=["atmo"] → `from atmosphere.db import …`
    returns None because "atmosphere.db" does not start with "atmo." and != "atmo"."""
    r = _dep_repo_nested(
        tmp_path,
        resolve_packages=["atmo"],
        subpkg="atmosphere/db",
        func_src=_RO_SRC,
    )
    call, tree = _call("from atmosphere.db import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


def test_unparseable_dep_init_returns_none(tmp_path):
    """A5: dep __init__.py with a syntax error → resolve_func returns None (graceful)."""
    r = _dep_repo(
        tmp_path,
        resolve_packages=["atmo"],
        dep_pkg="atmo",
        dep_src="def refresh_orms(:\n",  # intentional syntax error
    )
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


# ---------------------------------------------------------------------------
# Re-export following tests
# ---------------------------------------------------------------------------


def _dep_pkg(tmp_path, init_src, utils_src, *, resolve_packages=("atmo",)):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    pkg = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(init_src)
    (pkg / "utils.py").write_text(utils_src)
    return CalleeResolver(
        tmp_path,
        resolve_packages=tuple(resolve_packages),
        site_packages=find_site_packages(tmp_path),
    )


_RO_DEF = "def refresh_orms(s, objs):\n    for o in objs:\n        s.refresh(o)\n"


def test_follows_star_reexport(tmp_path):
    r = _dep_pkg(tmp_path, "from .utils import *\n", _RO_DEF)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_follows_explicit_relative_reexport(tmp_path):
    r = _dep_pkg(tmp_path, "from .utils import refresh_orms\n", _RO_DEF)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is not None


def test_follows_aliased_reexport(tmp_path):
    r = _dep_pkg(
        tmp_path,
        "from .utils import _ro as refresh_orms\n",
        "def _ro(s, objs):\n    for o in objs:\n        s.refresh(o)\n",
    )
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "_ro"


def test_follows_absolute_reexport(tmp_path):
    r = _dep_pkg(tmp_path, "from atmo.utils import refresh_orms\n", _RO_DEF)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is not None


def test_reexport_not_found_returns_none(tmp_path):
    r = _dep_pkg(
        tmp_path,
        "from .utils import something_else\n",
        "def something_else():\n    return 1\n",
    )
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


def test_reexport_cycle_terminates(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.py").write_text("from p.b import refresh_orms\n")
    (tmp_path / "p" / "b.py").write_text("from p.a import refresh_orms\n")
    r = CalleeResolver(tmp_path)
    call, tree = _call("from p.a import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


# ---------------------------------------------------------------------------
# _reexport_target unit tests
# ---------------------------------------------------------------------------


def _importfrom(src: str) -> ast.ImportFrom:
    node = ast.parse(src).body[0]
    assert isinstance(node, ast.ImportFrom)
    return node


def test_reexport_target_absolute():
    n = _importfrom("from atmo.utils import x\n")
    assert CalleeResolver._reexport_target("atmo.database", True, n) == "atmo.utils"


def test_reexport_target_relative_level1_package():
    n = _importfrom("from .utils import x\n")  # level 1
    # a package __init__ anchors at the package itself
    assert (
        CalleeResolver._reexport_target("atmo.database", True, n)
        == "atmo.database.utils"
    )


def test_reexport_target_relative_level1_module():
    n = _importfrom("from .utils import x\n")
    # a regular module anchors at its parent package
    assert (
        CalleeResolver._reexport_target("atmo.database.svc", False, n)
        == "atmo.database.utils"
    )


def test_reexport_target_relative_level2():
    n = _importfrom("from ..common import x\n")  # level 2
    # package "a.b.c": level 2 strips one more -> "a.b", + "common"
    assert CalleeResolver._reexport_target("a.b.c", True, n) == "a.b.common"


def test_reexport_target_out_of_bounds_returns_none():
    n = _importfrom("from ....deep import x\n")  # level 4, too deep for "a.b"
    assert CalleeResolver._reexport_target("a.b", True, n) is None


# ---------------------------------------------------------------------------
# Obscure edge-case tests — callee resolver boundary probes
# ---------------------------------------------------------------------------


def test_type_checking_block_import_resolves(tmp_path):
    """Case 1: `from app.helpers import reload` nested inside `if TYPE_CHECKING:` block.

    ast.walk visits ALL nodes (incl. nested If bodies), so the ImportFrom is found
    even though it's guarded. Result: resolves correctly — CORRECT behaviour.
    At runtime the import is dead code, so any false-negative introduced here is
    constrained to TYPE_CHECKING-only imports, which are always type stubs in practice.
    """
    r = _repo(tmp_path, {"app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n"})
    src = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.helpers import reload\n"
        "reload(s, o)\n"
    )
    call, tree = _call(src)
    fn = r.resolve_func(call, tree)
    # ast.walk sees the ImportFrom inside the If body — resolves to the real def.
    assert fn is not None and fn.name == "reload"


def test_multi_name_import_resolves_first_name(tmp_path):
    """Case 2: `from app.helpers import reload, other` — `reload(s, o)` resolves.

    _callee_origin iterates aliases in order; the matching alias (reload) is found
    immediately. CORRECT: multi-name from-imports work as expected.
    """
    r = _repo(
        tmp_path,
        {
            "app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n\ndef other(): pass\n"
        },
    )
    src = "from app.helpers import reload, other\nreload(s, o)\n"
    call, tree = _call(src)
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "reload"


def test_from_pkg_import_submodule_attr_call_resolves(tmp_path):
    """Case 3: `from app import helpers; helpers.reload(s, o)` — now resolves.

    `_callee_origin` uses `name_origin_map`, which records `from app import helpers`
    as `helpers -> app.helpers`.  `resolve_dotted('helpers.reload', {'helpers': 'app.helpers'})`
    → 'app.helpers.reload' → module 'app.helpers', name 'reload' → resolved on disk.
    """
    r = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n",
        },
    )
    src = "from app import helpers\nhelpers.reload(s, o)\n"
    call, tree = _call(src)
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "reload"


def test_aliased_module_import_resolves(tmp_path):
    """Case 3b: `import app.helpers as h; h.reload(s, o)` — resolves to reload.

    `import app.helpers as h` IS registered by import_alias_map (ast.Import with asname).
    resolve_dotted('h.reload', {'h': 'app.helpers'}) → 'app.helpers.reload'.
    CORRECT: aliased dotted-module imports work end-to-end.
    """
    r = _repo(tmp_path, {"app/helpers.py": "def reload(s, o):\n    s.refresh(o)\n"})
    src = "import app.helpers as h\nh.reload(s, o)\n"
    call, tree = _call(src)
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "reload"


def test_star_reexport_honors_all_dunder_excluded(tmp_path):
    """Case 4: `__all__` on the star-imported module gates what `*` re-exports.

    `atmo/__init__.py` does `from .utils import *`; `utils.py` defines `refresh_orms` but its
    `__all__` lists only `other`.  At runtime `from atmo import refresh_orms` would fail, and the
    resolver now honors that — `_star_exports` sees `refresh_orms` is not in utils' `__all__` →
    the star branch is skipped → returns None.
    """
    r = _dep_pkg(
        tmp_path,
        "from .utils import *\n",
        "__all__ = ['other']\n" + _RO_DEF + "\ndef other(): pass\n",
    )
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    assert r.resolve_func(call, tree) is None


def test_star_reexport_honors_all_dunder_included(tmp_path):
    """When the star-imported module's `__all__` DOES list the name, it resolves through the star."""
    r = _dep_pkg(
        tmp_path,
        "from .utils import *\n",
        "__all__ = ['refresh_orms']\n" + _RO_DEF,
    )
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_star_reexport_no_all_dunder_uses_public_default(tmp_path):
    """No `__all__` → `*` exposes public (non-underscore) names, so `refresh_orms` still resolves
    (the real cyclone shape)."""
    r = _dep_pkg(tmp_path, "from .utils import *\n", _RO_DEF)
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "refresh_orms"


def test_five_hop_chain_exceeds_depth_cap(tmp_path):
    """Case 5: a→b→c→d→e (5 hops) exceeds the depth-4 cap → returns None.

    resolve_func calls _find_def with depth=4.  Each re-export hop consumes one unit:
      p.a (depth=4) → re-export to p.b (depth=3) → p.c (depth=2) → p.d (depth=1) → p.e (depth=0).
    At depth=0 the guard fires before p.e is inspected → None.

    Also confirmed: a 4-hop chain (a→b→c→d, def in d) DOES resolve — the cap is
    inclusive of the initial call (depth=4 means 4 _find_def activations, not 4 hops).

    Classification: correct — the bound prevents unbounded recursion; depth-4 covers
    the vast majority of real re-export chains.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.py").write_text("from p.b import f\n")
    (tmp_path / "p" / "b.py").write_text("from p.c import f\n")
    (tmp_path / "p" / "c.py").write_text("from p.d import f\n")
    (tmp_path / "p" / "d.py").write_text("from p.e import f\n")
    (tmp_path / "p" / "e.py").write_text("def f(): pass\n")
    r = CalleeResolver(tmp_path)
    call, tree = _call("from p.a import f\nf()\n")
    # 5 hops: p.a(4)→p.b(3)→p.c(2)→p.d(1)→p.e(0) — depth guard fires at p.e
    assert r.resolve_func(call, tree) is None


def test_four_hop_chain_within_depth_cap(tmp_path):
    """Companion to Case 5: 4-hop chain (a→b→c→d, def in d) DOES resolve.

    _find_def activations: p.a(4)→p.b(3)→p.c(2)→p.d(1) — at depth=1 the def is
    found before any recursive call at depth=0.  Confirms the cap allows up to 4
    activations inclusive.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.py").write_text("from p.b import f\n")
    (tmp_path / "p" / "b.py").write_text("from p.c import f\n")
    (tmp_path / "p" / "c.py").write_text("from p.d import f\n")
    (tmp_path / "p" / "d.py").write_text("def f(): pass\n")
    r = CalleeResolver(tmp_path)
    call, tree = _call("from p.a import f\nf()\n")
    fn = r.resolve_func(call, tree)
    assert fn is not None and fn.name == "f"


def test_self_referential_star_import_terminates(tmp_path):
    """Case 6: `atmo/__init__.py: from . import *` — self-referential star terminates safely.

    `from . import *` with no module name: `_reexport_target('atmo', True, node)` returns
    'atmo' (the package itself) because `node.module` is None and the base resolves back to
    the current dotted name.  The subsequent _find_def('atmo', 'refresh_orms', ...) hits the
    seen-guard immediately (('atmo','refresh_orms') already in `seen`) and returns None.

    Classification: correct — terminates in O(1) extra work via the seen-guard; never crashes.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    sp = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    sp.mkdir(parents=True)
    (sp / "__init__.py").write_text("from . import *\n")
    r = CalleeResolver(
        tmp_path,
        resolve_packages=("atmo",),
        site_packages=find_site_packages(tmp_path),
    )
    call, tree = _call("from atmo import refresh_orms\nrefresh_orms(s, [o])\n")
    # Must not crash or loop; seen-guard stops the self-reference immediately.
    assert r.resolve_func(call, tree) is None
