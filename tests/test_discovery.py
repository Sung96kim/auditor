"""discovery.py: file enumeration, excludes, root resolution."""

import subprocess
from pathlib import Path

import pytest

from auditor.discovery import (
    FileDiscovery,
    default_base_ref,
    discover,
    find_root,
    git_changed_files,
)
from auditor.registry import REGISTRY


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


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


def test_tracked_but_deleted_file_is_skipped(tmp_path):
    # `git ls-files --cached` lists a tracked file even after it's deleted from the working tree
    # (deletion not staged). Discovery must skip it, not crash trying to read a missing path.
    _git(tmp_path, "init")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "gone.py").write_text("y = 2\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "x")
    (tmp_path / "gone.py").unlink()  # tracked, but no longer on disk

    files = {
        p.relative_to(tmp_path).as_posix() for p in discover(tmp_path, root=tmp_path)
    }
    assert files == {"a.py", "pyproject.toml"}  # pyproject.toml is scanned (config secrets)


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


def _migration_tree(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("x = 1\n")
    (tmp_path / "app" / "migrations").mkdir(parents=True)
    (tmp_path / "app" / "migrations" / "0001_init.py").write_text("x = 1\n")
    (tmp_path / "db" / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "db" / "alembic" / "versions" / "abc_rev.py").write_text("x = 1\n")
    return tmp_path


def test_migrations_soft_skipped_on_whole_repo(tmp_path):
    root = _migration_tree(tmp_path)
    files = {p.relative_to(root).as_posix() for p in FileDiscovery(root).files(root)}
    # migrations + alembic/versions dropped; pyproject.toml is scanned (config secrets)
    assert files == {"src/real.py", "pyproject.toml"}


def test_migrations_scanned_when_targeted(tmp_path):
    root = _migration_tree(tmp_path)
    # pointing at the migration dir overrides the soft-skip
    mig = {p.name for p in FileDiscovery(root).files(root / "app" / "migrations")}
    assert mig == {"0001_init.py"}
    ver = {
        p.name for p in FileDiscovery(root).files(root / "db" / "alembic" / "versions")
    }
    assert ver == {"abc_rev.py"}


def test_migration_single_file_scanned(tmp_path):
    root = _migration_tree(tmp_path)
    f = root / "app" / "migrations" / "0001_init.py"
    assert FileDiscovery(root).files(f) == [f]


def test_alembic_migration_dir_variants_soft_skipped(tmp_path):
    # regression: legacy/backup/manual alembic dirs (not just `versions`) are generated migrations
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("x = 1\n")
    for sub in ("versions", "versions_legacy", "versions_backup", "manual_migrations"):
        d = tmp_path / "alembic" / sub
        d.mkdir(parents=True)
        (d / "0001_rev.py").write_text("x = 1\n")
    files = {
        p.relative_to(tmp_path).as_posix()
        for p in FileDiscovery(tmp_path).files(tmp_path)
    }
    # every alembic migration dir variant dropped; pyproject.toml is scanned (config secrets)
    assert files == {"src/real.py", "pyproject.toml"}


def test_test_dir_named_migrations_is_not_soft_skipped(tmp_path):
    # regression: `tests/migrations/` holds tests OF migrations, not generated version files —
    # the soft-skip must not swallow it (it did, which also broke cross-file fixture-ref collection)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "tests" / "migrations").mkdir(parents=True)
    (tmp_path / "tests" / "migrations" / "test_upgrade.py").write_text(
        "def test_x():\n    assert True\n"
    )
    (tmp_path / "app" / "migrations").mkdir(parents=True)
    (tmp_path / "app" / "migrations" / "0001_init.py").write_text("x = 1\n")
    found = {
        p.relative_to(tmp_path).as_posix()
        for p in FileDiscovery(tmp_path).files(tmp_path)
    }
    assert "tests/migrations/test_upgrade.py" in found  # test code is scanned
    assert (
        "app/migrations/0001_init.py" not in found
    )  # real migration still soft-skipped


def test_parent_scan_still_skips_migrations(tmp_path):
    root = _migration_tree(tmp_path)
    # scanning the parent of a migrations dir (not the dir itself) still skips it
    files = {p.name for p in FileDiscovery(root).files(root / "app")}
    assert files == set()


def test_gitignore_respected_by_default(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "secret.py").write_text("y = 2\n")
    (tmp_path / ".gitignore").write_text("secret.py\n")
    files = {p.name for p in FileDiscovery(tmp_path).files(tmp_path)}
    assert files == {"a.py", "pyproject.toml"}  # gitignored file skipped; pyproject.toml scanned


def test_gitignore_can_be_disabled(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "secret.py").write_text("y = 2\n")
    (tmp_path / ".gitignore").write_text("secret.py\n")
    fd = FileDiscovery(tmp_path, respect_gitignore=False)
    assert {p.name for p in fd.files(tmp_path)} == {"a.py", "secret.py", "pyproject.toml"}


def test_manifest_discovered_by_filename_not_generic_json(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text('{"name": "x"}\n')  # a manifest — by filename
    (app / "tsconfig.json").write_text("{}\n")  # a generic .json — scanned by the config auditor
    (app / "index.py").write_text("x = 1\n")
    found = {p.name for p in FileDiscovery(tmp_path).files(app)}
    assert found == {"package.json", "index.py", "tsconfig.json"}
    # routing still distinguishes them: package.json -> manifest, tsconfig.json -> config
    assert REGISTRY.language_for_path("app/package.json").language == "manifest"
    assert REGISTRY.language_for_path("app/tsconfig.json").language == "config"


# ---------------------------------------------------------------------------
# New characterisation / coverage tests
# ---------------------------------------------------------------------------


def test_git_changed_files_non_git_dir(tmp_path):
    """git_changed_files returns None for a non-git directory."""
    (tmp_path / "a.py").write_text("x = 1\n")
    assert git_changed_files(tmp_path, "main") is None


def test_git_changed_files_bad_ref(tmp_path):
    """git_changed_files raises ValueError for a ref that cannot be resolved."""
    _git(tmp_path, "init")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    with pytest.raises(ValueError, match="could not be resolved"):
        git_changed_files(tmp_path, "no-such-ref-xyz-abc")


def test_git_changed_files_with_real_ref(tmp_path):
    """git_changed_files returns a set of relative paths for files changed since a ref."""
    _git(tmp_path, "init")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    # Edit one file after the commit — it's a working-tree diff from HEAD
    (tmp_path / "a.py").write_text("x = 99\n")
    changed = git_changed_files(tmp_path, "HEAD")
    assert isinstance(changed, set)
    assert "a.py" in changed
    # b.py was not changed
    assert "b.py" not in changed


def test_default_base_ref_non_git_dir(tmp_path):
    """default_base_ref returns None for a non-git directory."""
    assert default_base_ref(tmp_path) is None


def test_default_base_ref_on_main_branch(tmp_path):
    """default_base_ref returns 'main' (or 'origin/main') for a repo on a main branch."""
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    ref = default_base_ref(tmp_path)
    assert ref in ("main", "origin/main")
