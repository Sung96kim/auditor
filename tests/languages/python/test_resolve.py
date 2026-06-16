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
