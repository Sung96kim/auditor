"""The content secret sweep: any file a language auditor doesn't claim is still swept for
committed credentials (docs, data dumps, extensionless configs), while binaries are skipped."""

import subprocess

import pytest

from auditor.engine import audit_target

SECRET = "AKIAIOSFODNN7EXAMPLE"  # AWS access-key-id format
RULE = "CFG-SECRET-DETECTED"


def _repo(tmp_path, files: dict[str, bytes | str]):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content) if isinstance(content, bytes) else p.write_text(content)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
        capture_output=True,
    )
    return tmp_path


def _rule_map(results):
    return {r.file: {f.rule_id for f in r.findings} for r in results if r.findings}


@pytest.mark.parametrize(
    "name", ["README.md", "notes.txt", "data.sql", "Dockerfile", "config"]
)
async def test_secret_in_unclassified_file_is_flagged(tmp_path, name):
    repo = _repo(tmp_path, {name: f"credential = {SECRET}\n"})
    assert RULE in _rule_map(await audit_target(repo)).get(name, set())


async def test_binary_file_is_skipped(tmp_path):
    # a NUL byte marks it binary even though the secret bytes are present
    repo = _repo(tmp_path, {"logo.png": b"\x89PNG\x00" + SECRET.encode()})
    assert "logo.png" not in _rule_map(await audit_target(repo))


async def test_code_file_not_double_scanned(tmp_path):
    # a secret in .py is flagged once, by the Python sweep — not also by the content sweep
    repo = _repo(tmp_path, {"a.py": f'TOKEN = "{SECRET}"\n'})
    rules = _rule_map(await audit_target(repo)).get("a.py", set())
    assert "PY-SECRET-DETECTED" in rules and RULE not in rules


async def test_clean_files_produce_no_findings(tmp_path):
    repo = _repo(tmp_path, {"README.md": "# docs\nnothing secret here\n"})
    assert "README.md" not in _rule_map(await audit_target(repo))
