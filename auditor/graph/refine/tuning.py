"""Spec 11's knob tuning: the allow-list, the per-token stopword model, the build-time read and
the hand transitions.

A tuning row is not a refinement (spec 5.4): it carries no anchor, answers no queue row, and the
only thing it changes is one `GraphConfig` field the next build reads.
"""

import json
import re
import secrets
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from auditor.config import GraphConfig
from auditor.graph.refine.models import TuningRow, TuningStatus


class TuningRefused(RuntimeError):
    """A proposal or a transition the allow-list, the range, the cap or the mode will not take."""


class KnobSpec(BaseModel):
    """One allow-listed knob: the `GraphConfig` field it sets, its range, and whether S11 ships it."""

    model_config = ConfigDict(frozen=True)

    field: str
    low: float | None = None
    high: float | None = None
    shipped: bool = False


#: spec 11's allow-list in full. Only `stopwords` is shipped: on this repo the three numeric knobs
#: are either inert or catastrophic and nothing usable lies in between (P1).
TUNING_KNOBS: Final[dict[str, KnobSpec]] = {
    "stopwords": KnobSpec(field="stopwords", shipped=True),
    "name_similarity_threshold": KnobSpec(
        field="name_similarity_threshold", low=0.2, high=0.8
    ),
    "cluster_floor": KnobSpec(field="cluster_floor", low=0.2, high=0.8),
    "knn_k": KnobSpec(field="knn_k", low=4, high=16),
}

#: one stopword token: lowercase, starts with a letter, at most 40 characters
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: the confirmation word `graph tuning accept --token` checks, drawn from this alphabet. Named for
#: the letters and not for the token: auditor's own PY-SEC-HARDCODED-SECRET reads a literal bound
#: to a TOKEN-named constant as a secret, and `tests/test_dogfood.py` is where that shows up (P16)
_WORD_LETTERS: Final[str] = "abcdefghijkmnpqrstuvwxyz23456789"
TOKEN_LENGTH: Final[int] = 6


def confirmation_token() -> str:
    """A short unguessable word an accept has to repeat, so a copied id cannot activate a knob."""
    return "".join(secrets.choice(_WORD_LETTERS) for _ in range(TOKEN_LENGTH))


def knob(key: str) -> KnobSpec:
    """The allow-listed knob this key names, refused by name when it is not one (spec 9.2)."""
    spec = TUNING_KNOBS.get(key)
    if spec is None:
        raise TuningRefused(
            f"{key!r} is not tunable. Allow-list: {sorted(TUNING_KNOBS)}"
        )
    if not spec.shipped:
        raise TuningRefused(
            f"{key!r} is allow-listed but not shipped: measured on this repo it is either inert "
            "or moves the cluster count by more than 100 percent, with nothing in between"
        )
    return spec


def stopword(value: object) -> str:
    """One proposed stopword token, normalized, refused by name when it is not a word.

    Spec 11 tunes stopwords per token so a single word can be reverted without touching the rest.
    """
    if not isinstance(value, str):
        raise TuningRefused(
            f"a stopwords proposal is one token, not {type(value).__name__}"
        )
    token = value.strip().lower()
    if not _TOKEN.match(token):
        raise TuningRefused(
            f"{value!r} is not a stopword token: lowercase, letter first, at most 40 characters"
        )
    return token


def row_token(row: TuningRow) -> str:
    """The stopword one row proposes, refused by name when its stored value is not one.

    Only `stopwords` rows carry a token; a row for any other key answers "", so a malformed or
    numeric value never reaches `json.loads` as an exception (S11 L5).
    """
    if row.key != "stopwords":
        return ""
    try:
        return stopword(json.loads(row.value_json))
    except (TuningRefused, json.JSONDecodeError) as exc:
        raise TuningRefused(
            f"tuning {row.tuning_id} stores {row.value_json!r}, which is not a stopword token"
        ) from exc


def active_stopwords(rows: Sequence[TuningRow]) -> tuple[str, ...]:
    """The tokens the accepted rows add, sorted and deduplicated."""
    return tuple(
        sorted(
            {
                row_token(row)
                for row in rows
                if row.key == "stopwords" and row.status is TuningStatus.ACTIVE
            }
        )
    )


def candidate_stopwords(
    rows: Sequence[TuningRow], extra: Sequence[str] = ()
) -> tuple[str, ...]:
    """The stopword set the next build would use: every active token, plus the one a trial adds.

    The union is what makes a trial measure the config a build would produce rather than the new
    token alone, which is wrong in both directions once one token is already active (S11 E2).
    """
    return tuple(sorted({*active_stopwords(rows), *(stopword(e) for e in extra)}))


def tuned(cfg: GraphConfig, tokens: Sequence[str]) -> GraphConfig:
    """Spec 11's precedence: repo policy beats an active tuning row, which beats the default.

    `model_fields_set` is read on the repo's own config and never on a copy: no shipped profile
    carries a `[graph]` table, so a key in it is one the repo's own config set.
    """
    if not tokens or "stopwords" in cfg.model_fields_set:
        return cfg
    return cfg.model_copy(update={"stopwords": list(tokens)})
