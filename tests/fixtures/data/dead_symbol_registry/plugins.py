"""Subclass- and decorator-based registration (mirrors auditor's own Detector.__init_subclass__).

Defining a subclass of ``Plugin`` registers it into ``PLUGINS`` via ``__init_subclass__``; the
``@register`` decorator does the same. The subclass names are never referenced anywhere, but they
are NOT dead — the registration is the whole point, and deleting one drops it from ``PLUGINS``.
"""

from __future__ import annotations

PLUGINS: dict[str, type] = {}


class Plugin:
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        PLUGINS[cls.__name__] = cls


def register(cls: type) -> type:
    PLUGINS[cls.__name__] = cls
    return cls


# Registered just by being defined as a subclass — never referenced by name, but live.
class _AlphaPlugin(Plugin):
    code = "alpha"


# Registered by the decorator — never referenced by name, but live.
@register
class _BetaPlugin:
    code = "beta"


# A genuinely dead private class: no base, no decorator, never used. SHOULD be flagged.
class _UnusedHelper:
    pass
