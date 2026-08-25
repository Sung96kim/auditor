"""Plugin loading: entry points, config-named modules, and gated local repo plugins.

The plugin contract is the existing ABCs (Detector/LanguageAuditor/Reporter) — a loaded
module registers by subclassing. This module only finds and imports those modules. Local
``.auditor/plugins/*.py`` execute code, so they are gated behind ``trust_local_plugins``.
"""

import hashlib
import importlib
import importlib.util
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

from auditor.registry import REGISTRY

#: published groups, one per registry a plugin can add to (profiles are TOML, not classes)
_ENTRY_POINT_GROUPS = (
    "auditor.detectors",
    "auditor.languages",
    "auditor.reporters",
)


class PluginLoader:
    """Loads plugins from the three discovery mechanisms; records warnings."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.loaded: list[str] = []

    def load_entry_points(self) -> None:
        for group in _ENTRY_POINT_GROUPS:
            modules = [ep.module for ep in _entry_points(group)]
            for name in modules:
                self._import_target(name, listed=modules)

    def load_config_modules(self, module_names: list[str]) -> None:
        for name in module_names:
            self._import_target(name, listed=module_names)

    def load_local(self, root: Path, *, trusted: bool) -> None:
        plugin_dir = root / ".auditor" / "plugins"
        files = sorted(plugin_dir.glob("*.py")) if plugin_dir.is_dir() else []
        if not files:
            return
        if not trusted:
            self.warnings.append(
                f"{len(files)} local plugin file(s) in {plugin_dir} ignored "
                "(set trust_local_plugins=true or pass --allow-local-plugins to load them)"
            )
            return
        for file in files:
            self._import_path(file)

    # --- internals --------------------------------------------------------

    def _import_target(self, name: str, *, listed: Sequence[str] = ()) -> None:
        try:
            with REGISTRY.sources.sourcing(name, listed=listed):
                importlib.import_module(name)
            self.loaded.append(name)
        except Exception as exc:  # a broken plugin must not crash the auditor
            self.warnings.append(f"failed to load plugin {name!r}: {exc}")

    def _import_path(self, file: Path) -> None:
        mod_name = _local_module_name(file)
        if mod_name in sys.modules:  # already executed: re-running it would re-register
            self.loaded.append(str(file))
            return
        spec = importlib.util.spec_from_file_location(mod_name, file)
        if spec is None or spec.loader is None:
            self.warnings.append(f"could not load local plugin {file}")
            return
        try:
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            with REGISTRY.sources.sourcing(str(file)):
                spec.loader.exec_module(module)
            self.loaded.append(str(file))
        except Exception as exc:
            sys.modules.pop(mod_name, None)  # half-executed: never report it as loaded
            self.warnings.append(f"failed to load local plugin {file}: {exc}")


def _local_module_name(file: Path) -> str:
    """Module name for a local plugin file, keyed by its path so two repos shipping the same
    plugin filename stay separate modules."""
    digest = hashlib.sha256(str(file.resolve()).encode()).hexdigest()[:8]
    return f"auditor_local_plugin_{file.stem}_{digest}"


def _entry_points(group: str) -> Sequence[metadata.EntryPoint]:
    try:
        return metadata.entry_points(group=group)
    except TypeError:  # very old importlib.metadata signature
        return metadata.entry_points().get(group, [])
