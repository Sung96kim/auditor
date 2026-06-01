"""discovery.py: file enumeration, excludes, root resolution."""

from pathlib import Path

from auditor.discovery import FileDiscovery, discover, find_root


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "a.py").write_text("x = 1\n")
    (pkg / "sub" / "b.py").write_text("y = 2\n")
    (pkg / "thing_pb2.py").write_text("generated = 1\n")  # excluded (generated)
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "c.py").write_text("z = 3\n")  # excluded (cache dir)
    (pkg / "notes.txt").write_text("hi\n")  # not a .py
    return tmp_path


def test_find_root(tmp_path):
    (tmp_path / "pyproject.toml").write_text("\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_root(nested) == tmp_path


def test_discover_filters(tmp_path):
    root = _tree(tmp_path)
    files = {p.relative_to(root).as_posix() for p in discover(root / "pkg", root=root)}
    assert files == {"pkg/a.py", "pkg/sub/b.py"}


def test_discover_single_file(tmp_path):
    root = _tree(tmp_path)
    assert discover(root / "pkg" / "a.py", root=root) == [root / "pkg" / "a.py"]
    assert discover(root / "pkg" / "notes.txt", root=root) == []


def test_custom_exclude_glob(tmp_path):
    root = _tree(tmp_path)
    fd = FileDiscovery(root, exclude_globs=("**/sub/**",))
    files = {p.relative_to(root).as_posix() for p in fd.files(root / "pkg")}
    assert "pkg/sub/b.py" not in files
    assert "pkg/a.py" in files


def test_generated_typescript_is_excluded(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.tsx").write_text("export const App = () => null;\n")
    (src / "routeTree.gen.ts").write_text("export const tree = {};\n")
    (src / "schema.generated.ts").write_text("export type T = {};\n")
    (src / "types.d.ts").write_text("declare const x: number;\n")
    found = {p.name for p in FileDiscovery(tmp_path).files(src)}
    assert found == {"App.tsx"}  # generated/declaration files dropped
