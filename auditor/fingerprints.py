"""Per-rule fingerprints for cache invalidation.

A rule's cached findings on a file are valid only while the file content (sha256) AND the
rule's fingerprint are unchanged. The fingerprint folds in the detector's ``version`` and
the rule's *effective* config (enabled/severity/verdict/threshold), so editing one
threshold invalidates exactly that rule — not the whole cache.
"""

import hashlib
import json

from auditor.config import EffectiveRule
from auditor.registry import REGISTRY


def rule_fingerprint(rule_id: str, effective: EffectiveRule) -> str:
    detector = REGISTRY.detector(rule_id)
    payload = {
        "version": detector.version,
        "effective": effective.model_dump(mode="json"),
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def content_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()
