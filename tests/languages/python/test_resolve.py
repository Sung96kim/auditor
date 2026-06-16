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
