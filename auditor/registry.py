"""The runtime registry for detectors, languages, reporters, and categories.

A single cohesive ``Registry`` object owns the state (instead of loose module-level dicts
+ free functions). Built-in detectors register on import of their modules; plugins register
the same way. Kept dependency-light so the config layer can validate rule-ids/categories
against it without an import cycle.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from fnmatch import fnmatch
from typing import Any

from pydantic import BaseModel, ConfigDict

from auditor.models import Category, RuleId


class PluginSource(BaseModel):
    """The plugin a registration is credited to while its module executes."""

    model_config = ConfigDict(frozen=True)

    name: str
    listed: frozenset[str] = frozenset()

    def attribute(self, module: str | None) -> str:
        """Who a class defined in ``module`` belongs to: its own module when that module is a
        loaded plugin in its own right, else whichever plugin imported it."""
        return module if module is not None and module in self.listed else self.name


_BUILT_IN = PluginSource(name="built-in")
#: the plugin currently executing; a ContextVar so threads and tasks each restore their own
_CURRENT_SOURCE: ContextVar[PluginSource] = ContextVar(
    "auditor_plugin_source", default=_BUILT_IN
)


class SourceAttribution:
    """Which plugin (or ``built-in``) each detector/language/reporter was registered from.

    Registration happens while a plugin module executes, so the loader names the plugin around
    the import with ``sourcing()`` rather than at the registration call.
    """

    def __init__(self) -> None:
        self._sources: dict[str, str] = {}

    @contextmanager
    def sourcing(self, source: str, *, listed: Sequence[str] = ()) -> Iterator[None]:
        """Credit everything registered inside the block to ``source``, except classes defined by
        a module in ``listed`` (those are loaded plugins in their own right)."""
        token = _CURRENT_SOURCE.set(PluginSource(name=source, listed=frozenset(listed)))
        try:
            yield
        finally:
            _CURRENT_SOURCE.reset(token)

    def record(
        self,
        kind: str,
        name: str,
        *,
        module: str | None = None,
        source: str | None = None,
    ) -> None:
        """Credit ``kind:name`` to ``source`` when given, else to the plugin being loaded."""
        self._sources[f"{kind}:{name}"] = (
            source if source is not None else _CURRENT_SOURCE.get().attribute(module)
        )

    def source_of(self, kind: str, name: str) -> str:
        return self._sources.get(f"{kind}:{name}", "built-in")

    def table(self) -> dict[str, str]:
        return dict(self._sources)

    def load(self, table: Mapping[str, str]) -> None:
        self._sources = dict(table)


class RegistryState(BaseModel):
    """A copy of every registry table, restorable with ``Registry.restore``."""

    model_config = ConfigDict(frozen=True)

    detectors: dict[RuleId, type]
    languages: dict[str, type]
    reporters: dict[str, type]
    plugin_categories: set[str]
    sources: dict[str, str]


class Registry:
    """Holds every registered detector/language/reporter and the categories they declare."""

    def __init__(self) -> None:
        self._detectors: dict[RuleId, type] = {}
        self._languages: dict[str, type] = {}
        self._reporters: dict[str, type] = {}
        self._plugin_categories: set[str] = set()
        self.sources = SourceAttribution()

    # --- registration -----------------------------------------------------

    def register_detector(self, cls: type, *, source: str | None = None) -> None:
        existing = self._detectors.get(cls.rule_id)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"duplicate rule_id {cls.rule_id!r}: {existing!r} vs {cls!r}"
            )
        self._detectors[cls.rule_id] = cls
        self.sources.record(
            "detector", cls.rule_id, module=cls.__module__, source=source
        )
        if not isinstance(cls.category, Category) and cls.category not in {
            c.value for c in Category
        }:
            self._plugin_categories.add(str(cls.category))

    def register_language(self, cls: type, *, source: str | None = None) -> None:
        self._languages[cls.language] = cls
        self.sources.record(
            "language", cls.language, module=cls.__module__, source=source
        )

    def register_reporter(self, cls: type, *, source: str | None = None) -> None:
        self._reporters[cls.format] = cls
        self.sources.record(
            "reporter", cls.format, module=cls.__module__, source=source
        )

    # --- detector queries -------------------------------------------------

    def detector(self, rule_id: RuleId) -> type:
        return self._detectors[rule_id]

    def detectors(self) -> list[type]:
        return list(self._detectors.values())

    def detectors_for_language(self, language: str) -> list[type]:
        return [
            d
            for d in self._detectors.values()
            if getattr(d, "language", "python") == language
        ]

    def rule_ids(self) -> set[RuleId]:
        return set(self._detectors)

    def categories(self) -> set[str]:
        return {c.value for c in Category} | self._plugin_categories

    def frameworks(self) -> set[str]:
        return {
            fw
            for cls in self._detectors.values()
            if (fw := getattr(cls, "framework", None))
        }

    # --- language queries -------------------------------------------------

    def language(self, name: str) -> type | None:
        return self._languages.get(name)

    def languages(self) -> dict[str, type]:
        return dict(self._languages)

    def language_for_path(self, path: str) -> type | None:
        # filename match (e.g. ``package.json``) wins over a suffix match across all languages,
        # so a manifest isn't shadowed by a generic ``.json``/``.toml`` handler.
        name = path.rsplit("/", 1)[-1]
        for cls in self._languages.values():
            if any(fnmatch(name, pat) for pat in getattr(cls, "filenames", ())):
                return cls
        for cls in self._languages.values():
            if cls.extensions and path.endswith(tuple(cls.extensions)):
                return cls
        return None

    # --- reporter queries -------------------------------------------------

    def reporter(self, fmt: str) -> type | None:
        return self._reporters.get(fmt)

    def formats(self) -> set[str]:
        return set(self._reporters)

    # --- introspection / save-restore -------------------------------------

    def snapshot(  # auditor: skip: PY-TYPING-UNTYPED-DICT  (JSON boundary for `plugins list`)
        self,
    ) -> dict[str, Any]:
        """For ``auditor plugins list``."""
        return {
            "detectors": {
                rid: {
                    "category": str(cls.category),
                    "framework": getattr(cls, "framework", None),
                    "source": self.sources.source_of("detector", rid),
                }
                for rid, cls in sorted(self._detectors.items())
            },
            "languages": {
                name: {"source": self.sources.source_of("language", name)}
                for name in sorted(self._languages)
            },
            "reporters": {
                name: {"source": self.sources.source_of("reporter", name)}
                for name in sorted(self._reporters)
            },
        }

    def state(self) -> RegistryState:
        """A restorable copy of every table, for a test that loads a repo's plugins."""
        return RegistryState(
            detectors=dict(self._detectors),
            languages=dict(self._languages),
            reporters=dict(self._reporters),
            plugin_categories=set(self._plugin_categories),
            sources=self.sources.table(),
        )

    def restore(self, state: RegistryState) -> None:
        """Put every table back the way ``state`` found it."""
        self._detectors = dict(state.detectors)
        self._languages = dict(state.languages)
        self._reporters = dict(state.reporters)
        self._plugin_categories = set(state.plugin_categories)
        self.sources.load(state.sources)


#: process-wide singleton; everything registers into and queries this.
REGISTRY = Registry()
