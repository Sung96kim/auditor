import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_hooks_reference_existing_scripts():
    cfg = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(cfg) == {"SessionStart", "PostToolUse", "Stop"}
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
