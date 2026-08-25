"""`auditor init` — create and refresh the user config home."""

import json

import pytest
from _support import cli_json, invoke

from auditor import paths
from auditor.cli.init import CONFIG_VERSION
from auditor.paths import ensure_repo_dir, repo_dir, schema_ref_from
from auditor.user_settings import UserSettings


def test_init_writes_only_the_marker_keys(_isolated_auditor_home):
    payload = cli_json(invoke("init", "--json"))
    config = json.loads((_isolated_auditor_home / "config.json").read_text())
    assert config == {"$schema": "./config.schema.json", "config_version": 2}
    schema = json.loads((_isolated_auditor_home / "config.schema.json").read_text())
    assert "observer" in schema["properties"]
    assert "limits" in schema["$defs"]["ObserverConfig"]["properties"]
    assert (
        schema["$defs"]["LimitsConfig"]["properties"]["max_turns"]["description"]
        == "Agent turns before a run is cut off."
    )
    assert payload["home"] == str(_isolated_auditor_home)


def test_init_is_idempotent_and_keeps_user_keys(_isolated_auditor_home):
    invoke("init", "--json")
    path = _isolated_auditor_home / "config.json"
    path.write_text(json.dumps({"observer": {"runner": {"model": "sonnet"}}}))
    invoke("init", "--json")
    config = json.loads(path.read_text())
    assert config["observer"] == {"runner": {"model": "sonnet"}}
    assert config["$schema"] == "./config.schema.json"
    assert config["config_version"] == 2


def test_init_repo_writes_the_overlay_and_breadcrumb(tmp_path, _isolated_auditor_home):
    project = tmp_path / "project"
    project.mkdir()
    payload = cli_json(invoke("init", "--repo", "--root", str(project), "--json"))
    target = repo_dir(project)
    assert payload["repo_dir"] == str(target)
    assert json.loads((target / "config.json").read_text()) == {
        "$schema": "../../config.schema.json",
        "config_version": 2,
    }
    crumb = json.loads((target / "root.json").read_text())
    assert crumb["root"] == str(project.resolve())


def test_init_check_writes_nothing(tmp_path, _isolated_auditor_home):
    project = tmp_path / "project"
    project.mkdir()
    result = invoke("init", "--check", "--root", str(project), "--json")
    assert result.exit_code == 0
    assert not (_isolated_auditor_home / "config.json").exists()


def test_init_check_lists_unknown_user_keys(tmp_path, _isolated_auditor_home):
    (_isolated_auditor_home / "config.json").write_text(
        json.dumps({"observer": {"runer": "claude"}})
    )
    payload = cli_json(invoke("init", "--check", "--root", str(tmp_path), "--json"))
    assert payload["unknown_keys"] == ["observer.runer"]


def test_init_check_detects_a_moved_repo(tmp_path, _isolated_auditor_home):
    project = tmp_path / "project"
    project.mkdir()
    crumb = ensure_repo_dir(project) / "root.json"
    crumb.write_text(
        json.dumps({"root": str(tmp_path / "gone"), "identity": "x", "created_at": 1})
    )
    payload = cli_json(invoke("init", "--check", "--root", str(project), "--json"))
    assert payload["moved_from"] == str(tmp_path / "gone")
    assert json.loads(crumb.read_text())["root"] == str(tmp_path / "gone")


def test_init_migrate_requires_repo(tmp_path, _isolated_auditor_home):
    """--migrate rewrites the per-repo breadcrumb, so asking for it without --repo is a mistake,
    not a silent no-op."""
    result = invoke("init", "--migrate", "--root", str(tmp_path))
    assert result.exit_code == 1
    assert "--migrate requires --repo" in " ".join(result.output.split())


def test_init_migrate_rewrites_the_breadcrumb(tmp_path, _isolated_auditor_home):
    project = tmp_path / "project"
    project.mkdir()
    crumb = ensure_repo_dir(project) / "root.json"
    crumb.write_text(
        json.dumps({"root": str(tmp_path / "gone"), "identity": "x", "created_at": 1})
    )
    payload = cli_json(
        invoke("init", "--repo", "--migrate", "--root", str(project), "--json")
    )
    assert payload["moved_from"] == str(tmp_path / "gone")
    assert json.loads(crumb.read_text())["root"] == str(project.resolve())


def test_init_check_ignores_a_live_second_worktree(tmp_path, _isolated_auditor_home):
    """Two worktrees share the identity on purpose, so a live sibling root is not a move."""
    project = tmp_path / "project"
    sibling = tmp_path / "sibling"
    project.mkdir()
    sibling.mkdir()
    crumb = ensure_repo_dir(project) / "root.json"
    crumb.write_text(
        json.dumps({"root": str(sibling), "identity": "x", "created_at": 1})
    )
    payload = cli_json(invoke("init", "--check", "--root", str(project), "--json"))
    assert payload["moved_from"] is None


def test_init_reports_a_legacy_status_file(tmp_path, _isolated_auditor_home):
    legacy = tmp_path / ".auditor" / ".status.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    payload = cli_json(invoke("init", "--root", str(tmp_path), "--json"))
    assert payload["legacy_status"] == str(legacy)
    assert legacy.exists()  # reported, not deleted


def test_init_clean_status_deletes_the_legacy_file(tmp_path, _isolated_auditor_home):
    legacy = tmp_path / ".auditor" / ".status.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    payload = cli_json(
        invoke("init", "--clean-status", "--root", str(tmp_path), "--json")
    )
    assert payload["legacy_status"] == str(legacy)
    assert not legacy.exists()


def test_init_refuses_to_overwrite_a_torn_config(_isolated_auditor_home):
    """A JSON syntax error in the settings file must stop init, not be read as `{}` and then
    rewritten with only the marker keys (which silently deleted every user key)."""
    path = _isolated_auditor_home / "config.json"
    torn = '{"observer": {"max_cost_usd_per_day": 0.5},}'
    path.write_text(torn)
    result = invoke("init")
    assert result.exit_code == 1
    assert path.read_text() == torn
    assert str(path) in "".join(result.output.split())
    assert "Traceback" not in result.output


def test_init_check_reports_a_torn_config(_isolated_auditor_home):
    path = _isolated_auditor_home / "config.json"
    path.write_text("{oops")
    result = invoke("init", "--check")
    assert result.exit_code == 1
    assert "not valid JSON" in " ".join(result.output.split())


def test_init_refuses_to_overwrite_a_torn_repo_overlay(
    tmp_path, _isolated_auditor_home
):
    project = tmp_path / "project"
    project.mkdir()
    overlay = ensure_repo_dir(project) / "config.json"
    overlay.write_text("[]")  # valid JSON, wrong shape
    result = invoke("init", "--repo", "--root", str(project))
    assert result.exit_code == 1
    assert overlay.read_text() == "[]"


@pytest.mark.parametrize("flag", ["--migrate", "--clean-status"])
def test_init_write_flags_reject_check(tmp_path, _isolated_auditor_home, flag):
    """--check writes nothing, so pairing it with a flag whose whole job is a write is the same
    mistake --migrate without --repo already refuses, not a silent no-op."""
    result = invoke("init", "--repo", flag, "--check", "--root", str(tmp_path))
    assert result.exit_code == 1
    assert f"{flag} writes" in " ".join(result.output.split())


def test_init_check_payload_says_nothing_was_attempted(
    tmp_path, _isolated_auditor_home
):
    payload = cli_json(invoke("init", "--check", "--root", str(tmp_path), "--json"))
    assert payload["checked"] is True
    assert payload["written"] == []


def test_init_migrate_marks_the_breadcrumb_as_repointed(
    tmp_path, _isolated_auditor_home
):
    """After a successful --migrate the payload has to say so, or the renderer keeps telling the
    user to re-run the flag that just worked."""
    project = tmp_path / "project"
    project.mkdir()
    crumb = ensure_repo_dir(project) / "root.json"
    crumb.write_text(
        json.dumps({"root": str(tmp_path / "gone"), "identity": "x", "created_at": 1})
    )
    payload = cli_json(
        invoke("init", "--repo", "--migrate", "--root", str(project), "--json")
    )
    assert payload["migrated"] is True
    assert (
        cli_json(invoke("init", "--check", "--root", str(project), "--json"))[
            "migrated"
        ]
        is False
    )


def test_init_repo_resolves_the_git_identity_once(git_repo, monkeypatch):
    """Each identity lookup shells out to git, and init used to do four of them: the moved-repo
    check, ensure_repo_dir, the payload path, and the unknown-key scan."""
    calls = []
    real = paths.git_output

    def counted(root, *args):
        calls.append(args)
        return real(root, *args)

    monkeypatch.setattr(paths, "git_output", counted)
    result = invoke("init", "--repo", "--root", str(git_repo), "--json")
    assert result.exit_code == 0, result.output
    assert len(calls) == 1


def test_init_schema_refs_come_from_the_layout_helper(tmp_path, _isolated_auditor_home):
    """Asserting the literal pinned the geometry in the wrong module: a change to repos/<key>
    depth would leave both files pointing at nothing, with every test still green."""
    project = tmp_path / "project"
    project.mkdir()
    invoke("init", "--repo", "--root", str(project), "--json")
    overlay = repo_dir(project) / "config.json"
    assert json.loads((_isolated_auditor_home / "config.json").read_text())[
        "$schema"
    ] == schema_ref_from(_isolated_auditor_home)
    assert json.loads(overlay.read_text())["$schema"] == schema_ref_from(overlay.parent)
    assert (overlay.parent / json.loads(overlay.read_text())["$schema"]).resolve() == (
        _isolated_auditor_home / "config.schema.json"
    )


def test_config_version_marker_tracks_the_model_default():
    """Two sources of truth for one number: a bump on either side alone writes a marker the
    model rejects."""
    assert UserSettings.model_fields["config_version"].default == CONFIG_VERSION
