"""Consumer that reads the populated registries — so BP_REGISTRY / PLUGINS are live, and the
tag constants + plugin subclasses matter only through the side effect of having been defined."""

from plugins import PLUGINS
from tags import BP_REGISTRY


def known_tag_ids() -> list[str]:
    return sorted(BP_REGISTRY)


def known_plugins() -> list[str]:
    return sorted(PLUGINS)
