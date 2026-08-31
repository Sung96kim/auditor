import ast
import json
from pathlib import Path

import pytest

import auditr_observer

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
    assuming it.

    `ast.parse` returns a `Module` or raises, so the assertion is the call: named as such rather
    than dressed in an `isinstance` that cannot be false.
    """
    for script in (ROOT / "plugin" / "hooks").glob("*.py"):
        ast.parse(script.read_text(), feature_version=(3, 11))


@pytest.mark.parametrize(
    ("script", "event"),
    [
        ("session_start", "session-start"),
        ("audit_edit", "post-tool-use"),
        ("verify_stop", "stop"),
        ("session_end", "session-end"),
    ],
)
def test_each_hooks_timeout_covers_the_budget_its_event_spends(
    hook_module, script: str, event: str
):
    """The relationship five policy numbers across two processes describe and none enforced.

    `observe` runs the client under `subprocess.run(timeout=OBSERVE_TIMEOUT)`, so a parent whose
    budget is smaller than the sum of the child's own request budgets kills the client every
    time on the slow path. `session-start` is the deliberate exception: the `ensure` launch
    behind it may outrun the whole hook and the next Stop's heartbeat repairs it (P30), so it
    covers the attach it makes and not the daemon start behind it.
    """
    budget = auditr_observer.HOOK_BUDGETS[event]
    timeout = hook_module(script).OBSERVE_TIMEOUT
    assert timeout >= budget if event == "session-start" else timeout > budget


def test_the_stop_budget_is_the_sum_of_what_a_stop_actually_posts():
    """A heartbeat, the attach that repairs a session the daemon lost, and the batch itself."""
    assert auditr_observer.HOOK_BUDGETS["stop"] == (
        auditr_observer._POST_TIMEOUT
        + auditr_observer._REPAIR_TIMEOUT
        + auditr_observer._STOP_POST_TIMEOUT
    )
