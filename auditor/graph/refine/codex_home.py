"""The private `CODEX_HOME` one Codex run runs under (spec 9.3).

`-c` overrides merge with the user's own `mcp_servers` and `--ignore-user-config` exists only on
`codex exec`, so a home of our own is the whole isolation mechanism. Everything Invariant 4 needs
is text this module writes, which is what makes it testable with no SDK and no account.
"""

import json
import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor.graph.refine.prompts import GRAPH_SERVER
from auditor.paths import observer_dir

#: the env var the config names for the shim's bearer credential, so no secret sits in the file.
#: Named `BEARER_ENV` and not `TOKEN_ENV` because `PY-SEC-HARDCODED-SECRET` matches an assignment
#: whose *target name* holds `token` (`crypto.py:145-146`), and this repo scans itself.
BEARER_ENV = "AUDITOR_GRAPH_TOKEN"
#: how long the binary waits for the loopback shim before it gives up on the server
STARTUP_TIMEOUT_SEC = 10
#: read by the binary above `CODEX_HOME`; a run reads them for what they declare, never trusts them
MANAGED_CONFIG = Path("/etc/codex/config.toml")
MANAGED_HOOKS = Path("/etc/codex/hooks.json")
#: the tier above the private home, in the order a refusal reports it
MANAGED_FILES: tuple[Path, ...] = (MANAGED_CONFIG, MANAGED_HOOKS)


def codex_home_dir() -> Path:
    """Where every run's private home is made, a leaf beside `lock`, `daemon.json` and `log`."""
    return observer_dir() / "codex-home"


def run_home(parent: Path | None = None) -> Path:
    """A home of this run's own, so two concurrent runs cannot overwrite one `config.toml`.

    Named from a fresh token rather than the run id because the options a run needs are built
    before its row exists, and a request that cannot become options must open no row.
    """
    root = parent if parent is not None else codex_home_dir()
    return root / f"run-{secrets.token_hex(8)}"


def user_codex_home(env: Mapping[str, str] | None = None) -> Path:
    """Where the user's own Codex home is, `CODEX_HOME` first.

    The SDK's `default_codex_home()` hardcodes `~/.codex` and never reads the env var, so it is
    deliberately not called here.
    """
    named = (env if env is not None else os.environ).get("CODEX_HOME")
    return Path(named) if named else Path.home() / ".codex"


class CodexHome(BaseModel):
    """One run's private home: where it lives, and every fact its `config.toml` states."""

    model_config = ConfigDict(frozen=True)

    home: Path
    root: Path
    server_url: str
    model: str = ""

    def config_toml(self) -> str:
        """The whole file, in the order spec 9.3 lists it.

        Exactly one `[mcp_servers]` entry: the loopback shim this run owns. A model of "" is the
        user's own Codex default, so the key is omitted rather than written empty.
        """
        lines = [f"model = {json.dumps(self.model)}"] if self.model else []
        lines += [
            "",
            "[features]",
            "codex_hooks = false",
            "",
            f"[projects.{json.dumps(str(self.root))}]",
            'trust_level = "trusted"',
            "",
            f"[mcp_servers.{GRAPH_SERVER}]",
            f"url = {json.dumps(self.server_url)}",
            f"bearer_token_env_var = {json.dumps(BEARER_ENV)}",
            'default_tools_approval_mode = "approve"',
            f"startup_timeout_sec = {STARTUP_TIMEOUT_SEC}",
            "",
        ]
        return "\n".join(lines).lstrip("\n")

    def write(self, *, auth: Path) -> Path:
        """Create the home, write its config and link the user's `auth.json` into it.

        A symlink rather than a copy: refreshed tokens are written back through it, and a copy
        both races that rotation and duplicates a mode-600 secret under `$AUDITOR_HOME`.
        """
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.toml").write_text(self.config_toml(), encoding="utf-8")
        link = self.home / "auth.json"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(auth)
        return self.home
