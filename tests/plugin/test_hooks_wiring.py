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


def _hook_payload(event: str, root: Path) -> dict:
    """The smallest client payload that drives one event down its whole chain."""
    base = {"session_id": "s1", "cwd": str(root)}
    if event == "post-tool-use":
        return {**base, "tool_input": {"file_path": str(root / "m.py")}}
    return base


@pytest.mark.parametrize(
    "event", ["session-start", "post-tool-use", "stop", "session-end"]
)
def test_every_deadline_an_event_hands_out_fits_inside_its_budget(
    event: str, git_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """Spied on a real run, because the budget has to name every deadline and not only the wire.

    The shape this replaces re-added `_POST_TIMEOUT + _REPAIR_TIMEOUT + _STOP_POST_TIMEOUT` back
    up, so it agreed with `HOOK_BUDGETS["stop"]` by construction and could not see the git
    subprocesses at all - and those are the largest term and the only deadlines that fall
    *before* the batch is on disk, where a parent's kill loses it whole (L2, M2).
    """
    (git_repo / "m.py").write_text("x = 1\n")
    real_git = auditr_observer._git
    spent: list[tuple[str, float]] = []

    def spy_git(root: Path, *args: str, timeout: float) -> str | None:
        spent.append((args[0], timeout))
        return real_git(root, *args, timeout=timeout)

    monkeypatch.setattr(auditr_observer, "_run", lambda command: {})
    monkeypatch.setattr(auditr_observer, "_git", spy_git)
    monkeypatch.setattr(
        auditr_observer,
        "_post",
        # `ok: False` is what makes a Stop take its repair attach, which is the worst case
        lambda path, body, timeout: (
            spent.append((path, timeout)) or (202, {"ok": False})
        ),
    )
    auditr_observer._hook(event, "claude-code", _hook_payload(event, git_repo))
    assert spent, "an event that hands out no deadline at all cannot be budgeted"
    assert sum(timeout for _, timeout in spent) <= auditr_observer.HOOK_BUDGETS[event]
