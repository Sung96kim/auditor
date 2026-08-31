import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_hooks_reference_existing_scripts():
    cfg = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(cfg) == {"SessionStart", "PostToolUse", "Stop", "SessionEnd"}
    for event in cfg.values():
        for group in event:
            for hook in group["hooks"]:
                script = (
                    hook["command"]
                    .split()[-1]
                    .replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT / "plugin"))
                )
                assert Path(script).exists(), script


def test_posttooluse_matches_edit_write():
    cfg = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
    assert cfg["PostToolUse"][0]["matcher"] == "Edit|Write"


def test_session_end_runs_the_detach_hook():
    """Spec 13.1's fourth event: without it the daemon only loses a session on expiry."""
    cfg = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for g in cfg["SessionEnd"] for h in g["hooks"]]
    assert commands == ["python3 ${CLAUDE_PLUGIN_ROOT}/hooks/session_end.py"]


def test_every_wired_event_is_one_claude_code_knows():
    """The bundled hooks doc's own list; a typo here is a hook that silently never fires."""
    known = {
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "Notification",
        "UserPromptSubmit",
        "SessionStart",
        "SessionEnd",
        "Stop",
        "PreCompact",
        "PostCompact",
    }
    cfg = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(cfg) <= known


def test_every_hook_script_parses_on_the_hooks_floor():
    """`session_start.py` imports `tomllib`, so the hooks floor is 3.11; the statusline's is 3.9
    and `tests/plugin/test_statusline.py` holds it there. Pin the second floor rather than
    assuming it."""
    for script in (ROOT / "plugin" / "hooks").glob("*.py"):
        assert isinstance(
            ast.parse(script.read_text(), feature_version=(3, 11)), ast.Module
        ), script
