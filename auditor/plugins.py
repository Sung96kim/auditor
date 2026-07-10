"""Plugin loading: entry points, config-named modules, and gated local repo plugins.

The plugin contract is the existing ABCs (Detector/LanguageAuditor/Reporter) — a loaded
module registers by subclassing. This module only finds and imports those modules. Local
``.auditor/plugins/*.py`` execute code, so they are gated behind ``trust_local_plugins``.
"""

import importlib
import importlib.util
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

_ENTRY_POINT_GROUPS = (
    "auditor.detectors",
    "auditor.languages",
    "auditor.reporters",
    "auditor.profiles",
)


class PluginLoader:
    """Loads plugins from the three discovery mechanisms; records warnings."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.loaded: list[str] = []

    def load_entry_points(self) -> None:
        for group in _ENTRY_POINT_GROUPS:
            for ep in _entry_points(group):
                self._import_target(ep)

    def load_config_modules(self, module_names: list[str]) -> None:
        for name in module_names:
            self._import_target(name)

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

    def _import_target(self, name: str) -> None:
        try:
            importlib.import_module(name)
            self.loaded.append(name)
        except Exception as exc:  # a broken plugin must not crash the auditor
            self.warnings.append(f"failed to load plugin {name!r}: {exc}")

    def _import_path(self, file: Path) -> None:
        mod_name = f"auditor_local_plugin_{file.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, file)
        if spec is None or spec.loader is None:
            self.warnings.append(f"could not load local plugin {file}")
            return
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.loaded.append(str(file))
        except Exception as exc:
            self.warnings.append(f"failed to load local plugin {file}: {exc}")


def _entry_points(group: str) -> Sequence[metadata.EntryPoint]:
    try:
        return metadata.entry_points(group=group)
    except TypeError:  # very old importlib.metadata signature
        return metadata.entry_points().get(group, [])
