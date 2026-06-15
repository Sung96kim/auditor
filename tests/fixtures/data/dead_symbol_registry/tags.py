"""Blueprint tag registry (mirrors orion's services/workflows/component_tags.py).

Each ``BlueprintTagMapping`` self-registers into ``BP_REGISTRY`` on construction, so the
module-level tag constants below are never referenced by name — yet they are NOT dead: deleting
one removes it from the registry and changes behavior. PY-DEAD-SYMBOL must not flag a binding
whose value is built by a call, because the call may have side effects.

``LEGACY_MAX_TAGS`` is a plain literal with no construction effect and is genuinely unused — that
one is a real dead constant and should still be flagged.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum


class Category(str, Enum):
    provider = "provider"
    agent_type = "agent_type"


BP_REGISTRY: dict[str, BlueprintTagMapping] = {}
BP_REGISTRY_BY_CATEGORY: dict[Category, list[BlueprintTagMapping]] = defaultdict(list)


class BlueprintTagMapping:
    def __init__(self, tag_id: str, category: Category) -> None:
        self.tag_id = tag_id
        self.category = category
        if tag_id in BP_REGISTRY:
            raise ValueError(f"{tag_id} is already registered")
        BP_REGISTRY[tag_id] = self
        BP_REGISTRY_BY_CATEGORY[category].append(self)


# Never referenced by name — present purely for the registration side effect in __init__.
ACCELERATOR = BlueprintTagMapping("accelerator", Category.provider)
EXTRACTION = BlueprintTagMapping("extraction", Category.agent_type)
CLASSIFICATION = BlueprintTagMapping("classification", Category.agent_type)

# A genuinely dead literal constant — no construction effect, never used anywhere.
LEGACY_MAX_TAGS = 64
