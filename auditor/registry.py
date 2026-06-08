"""The runtime registry for detectors, languages, reporters, and categories.

A single cohesive ``Registry`` object owns the state (instead of loose module-level dicts
+ free functions). Built-in detectors register on import of their modules; plugins register
the same way. Kept dependency-light so the config layer can validate rule-ids/categories
against it without an import cycle.
"""

from fnmatch import fnmatch
from typing import Any

from auditor.models import Category, RuleId


class Registry:
    """Holds every registered detector/language/reporter and the categories they declare."""

    def __init__(self) -> None:
        self._detectors: dict[RuleId, type] = {}
        self._languages: dict[str, type] = {}
        self._reporters: dict[str, type] = {}
        self._plugin_categories: set[str] = set()
        self._sources: dict[str, str] = {}

    # --- registration -----------------------------------------------------

    def register_detector(self, cls: type, *, source: str = "built-in") -> None:
        existing = self._detectors.get(cls.rule_id)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"duplicate rule_id {cls.rule_id!r}: {existing!r} vs {cls!r}"
            )
        self._detectors[cls.rule_id] = cls
        self._sources[f"detector:{cls.rule_id}"] = source
        if not isinstance(cls.category, Category) and cls.category not in {
            c.value for c in Category
        }:
            self._plugin_categories.add(str(cls.category))

    def register_language(self, cls: type, *, source: str = "built-in") -> None:
        self._languages[cls.language] = cls
        self._sources[f"language:{cls.language}"] = source

    def register_reporter(self, cls: type, *, source: str = "built-in") -> None:
        self._reporters[cls.format] = cls
        self._sources[f"reporter:{cls.format}"] = source

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

    # --- provenance / introspection --------------------------------------

    def source_of(self, kind: str, name: str) -> str:
        return self._sources.get(f"{kind}:{name}", "built-in")

    def snapshot(self) -> dict[str, Any]:  # auditor: skip: PY-TYPING-UNTYPED-DICT  (JSON boundary for `plugins list`)
        """For ``auditor plugins list``."""
        return {
            "detectors": {
                rid: {
                    "category": str(cls.category),
                    "framework": getattr(cls, "framework", None),
                    "source": self.source_of("detector", rid),
                }
                for rid, cls in sorted(self._detectors.items())
            },
            "languages": {
                name: {"source": self.source_of("language", name)}
                for name in sorted(self._languages)
            },
            "reporters": {
                name: {"source": self.source_of("reporter", name)}
                for name in sorted(self._reporters)
            },
        }


#: process-wide singleton; everything registers into and queries this.
REGISTRY = Registry()
