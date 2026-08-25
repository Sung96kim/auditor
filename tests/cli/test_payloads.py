"""The `--json` key contract, pinned per command.

Every list was captured from the CLI before the payload models landed, so a retyped payload that
renames, drops or adds a key fails here rather than silently breaking an agent that parses it.
"""

import json

import pytest
from _support import cli_json, invoke
from pydantic import ValidationError

from auditor.cli.helpers import present
from auditor.cli.payloads import (
    CrossfileReport,
    DetectorInfo,
    PluginsReport,
    SourceInfo,
)


@pytest.fixture
def plain_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("class Foo:\n    def bar(self):\n        return 1\n")
    return tmp_path


OBJECT_KEYS: dict[str, list[str]] = {
    "crossfile": ["cross_file_findings"],
    "config check": ["policy_unknown", "root", "user_unknown"],
    "init": [
        "checked",
        "config",
        "home",
        "legacy_status",
        "migrated",
        "moved_from",
        "repo_dir",
        "schema",
        "unknown_keys",
        "written",
    ],
}
ROW_KEYS: dict[str, list[str]] = {
    "discover": ["file", "role"],
    "manifest": [
        "arg_count",
        "decorators",
        "field_count",
        "flags",
        "is_async",
        "kind",
        "line",
        "return_type",
        "symbol",
    ],
}


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("crossfile", ["crossfile"]),
        ("config check", ["config", "check", "-r"]),
        ("init", ["init", "--check", "-r"]),
    ],
)
def test_object_payload_keys_are_unchanged(plain_repo, name, argv):
    payload = cli_json(invoke(*argv, str(plain_repo), "--json"))
    assert sorted(payload) == OBJECT_KEYS[name]


def test_discover_row_keys_are_unchanged(plain_repo):
    payload = cli_json(invoke("discover", str(plain_repo), "--json"))
    assert payload and sorted(payload[0]) == ROW_KEYS["discover"]


def test_manifest_row_keys_are_unchanged(plain_repo):
    payload = cli_json(invoke("manifest", str(plain_repo / "a.py"), "--json"))
    assert payload and sorted(payload[0]) == ROW_KEYS["manifest"]


def test_config_show_json_is_the_settings_model(plain_repo):
    payload = cli_json(invoke("config", "show", "-r", str(plain_repo), "--json"))
    assert payload["extends"] == "base"
    assert "unknown_keys" not in payload  # the loader's field never reaches the wire


def test_present_serialises_a_model_not_a_dict(capsys):
    """The contract: `present` owns the dump, so no command hand-rolls `model_dump`."""
    present(CrossfileReport(cross_file_findings=3), lambda out, p: None, as_json=True)
    assert json.loads(capsys.readouterr().out) == {"cross_file_findings": 3}


def test_present_emits_an_empty_object_for_a_missing_payload(capsys):
    """`None` is the "nothing found" payload; `null` would change the wire contract."""
    present(None, lambda out, p: None, as_json=True)
    assert json.loads(capsys.readouterr().out) == {}


@pytest.fixture
def scanned_repo(plain_repo):
    """A repo with one indexed file and one persistent ignore, so the list commands are non-empty."""
    assert invoke("scan", str(plain_repo), "-i").exit_code == 0
    assert (
        invoke(
            "index", "add", str(plain_repo / "a.py"), "-r", str(plain_repo)
        ).exit_code
        == 0
    )
    assert (
        invoke(
            "ignore", "add", "PY-TYPING-MISSING-HINTS", "-r", str(plain_repo)
        ).exit_code
        == 0
    )
    return plain_repo


def test_index_add_keys_are_unchanged(plain_repo):
    payload = cli_json(
        invoke(
            "index", "add", str(plain_repo / "a.py"), "-r", str(plain_repo), "--json"
        )
    )
    assert sorted(payload) == ["added"]


def test_index_forget_keys_are_unchanged(scanned_repo):
    payload = cli_json(
        invoke("index", "forget", "-r", str(scanned_repo), "--yes", "--json")
    )
    assert sorted(payload) == ["removed", "repo"]


def test_ignore_clear_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("ignore", "clear", "-r", str(scanned_repo), "--json"))
    assert sorted(payload) == ["cleared"]


def test_index_list_row_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("index", "list", "-r", str(scanned_repo), "--json"))
    assert payload and sorted(payload[0]) == [
        "counts",
        "doc_path",
        "language",
        "last_scanned",
        "lines",
        "path",
        "role",
        "sha256",
    ]


def test_index_repos_row_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("index", "repos", "--json"))
    assert payload and sorted(payload[0]) == ["last_scanned", "name", "repo"]


def test_ignore_list_row_keys_are_unchanged(scanned_repo):
    payload = cli_json(invoke("ignore", "list", "-r", str(scanned_repo), "--json"))
    assert payload and sorted(payload[0]) == [
        "created_at",
        "evidence_hash",
        "file",
        "id",
        "line",
        "reason",
        "rule_id",
    ]


def test_ignore_add_keys_are_unchanged(plain_repo):
    payload = cli_json(
        invoke(
            "ignore", "add", "PY-TYPING-MISSING-HINTS", "-r", str(plain_repo), "--json"
        )
    )
    assert sorted(payload) == ["file", "id", "line", "note", "reason", "rule_id"]


def test_ignore_rm_keys_are_unchanged(plain_repo):
    invoke("ignore", "add", "PY-TYPING-MISSING-HINTS", "-r", str(plain_repo))
    payload = cli_json(invoke("ignore", "rm", "1", "-r", str(plain_repo), "--json"))
    assert sorted(payload) == ["removed", "selector"]


def test_rules_list_row_keys_are_unchanged():
    payload = cli_json(invoke("rules", "list", "--json"))
    assert sorted(payload[0]) == [
        "category",
        "default_severity",
        "framework",
        "rule_id",
        "source",
        "standard_refs",
        "verdict_kind",
    ]


def test_plugins_list_keys_are_unchanged(plain_repo):
    payload = cli_json(invoke("plugins", "list", "-r", str(plain_repo), "--json"))
    assert sorted(payload) == ["detectors", "languages", "reporters", "warnings"]


def test_plugins_list_detector_entry_keys_are_unchanged(plain_repo):
    """The per-entry shape is the half `test_plugins_list_keys_are_unchanged` cannot see, and it
    is the half the bug batch may widen."""
    payload = cli_json(invoke("plugins", "list", "-r", str(plain_repo), "--json"))
    first = payload["detectors"][next(iter(payload["detectors"]))]
    assert sorted(first) == ["category", "framework", "source"]


@pytest.mark.parametrize(
    ("model", "raw", "unknown"),
    [
        (DetectorInfo, {"category": "security", "source": "built-in"}, "hits"),
        (SourceInfo, {"source": "built-in"}, "hits"),
        (
            PluginsReport,
            {"detectors": {}, "languages": {}, "reporters": {}},
            "formatters",
        ),
    ],
)
def test_a_registry_key_no_model_declares_fails_loudly(model, raw, unknown):
    """`extra="forbid"` is the whole guard: `REGISTRY.snapshot()` is untyped, so a section or an
    entry field it gains has to raise here rather than be dropped on the way to the wire."""
    assert model.model_validate(raw)  # the declared shape still validates
    with pytest.raises(ValidationError, match=unknown):
        model.model_validate({**raw, unknown: {}})
