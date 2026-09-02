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
from auditor.database import IndexStore
from auditor.graph.refine.models import TuningMetrics, TuningRow, TuningStatus


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


#: statuses a hand transition may leave (spec 5.8); everything else is terminal
_ACCEPT_FROM = frozenset({TuningStatus.PENDING})
_REVERT_FROM = frozenset({TuningStatus.PENDING, TuningStatus.ACTIVE})
#: a trial may look at a row that is waiting and at one a guard already refused, and at nothing
#: else: re-measuring an active or reverted row would overwrite the metrics Invariant 3 keeps
MEASURE_FROM = frozenset({TuningStatus.PENDING, TuningStatus.REJECTED})

#: one proposal per key per day (spec 11)
PROPOSAL_WINDOW_SECONDS: Final[float] = 86400.0

#: the statuses that still claim a key: a reverted or superseded row is not a live proposal and
#: must not hold the key's daily slot (S11 L4)
LIVE_STATUSES = frozenset({TuningStatus.PENDING, TuningStatus.ACTIVE})


class TuningLedger(BaseModel):
    """Spec 5.8's hand transitions over one index handle, and the reads every surface shares.

    A ledger rather than a service because none of it needs a checkout: accepting a knob reads and
    writes one row and touches no file, no run registry and no git.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    index: IndexStore

    async def rows(
        self, *, statuses: Sequence[TuningStatus] | None = None
    ) -> list[TuningRow]:
        return await self.index.tuning.tuning(statuses=statuses)

    async def row(self, ident: str) -> TuningRow:
        """One row by id or by the token it proposes, refused by name rather than answered None.

        Spec 12.2 writes `<id|key>`; with one row per stopword token the useful second spelling is
        the token itself, because `stopwords` names every row at once (P10).
        """
        rows = await self.rows()
        if ident.isdigit():
            found = [r for r in rows if r.tuning_id == int(ident)]
        else:
            found = [
                r
                for r in rows
                if r.status in LIVE_STATUSES
                and r.key == "stopwords"
                and row_token(r) == ident
            ]
        if not found:
            raise TuningRefused(f"no tuning row {ident!r} on this checkout")
        return found[-1]

    async def accept(self, ident: str, *, token: str, cap: int) -> TuningRow:
        """Activate a measured, guard-passing proposal. The next `graph build` reads it."""
        row = await self.row(ident)
        _judged(row)
        _checked(row, token, _ACCEPT_FROM, TuningStatus.ACTIVE)
        active = await self.rows(statuses=[TuningStatus.ACTIVE])
        check_cap(active, cap)
        return await self._move(row, TuningStatus.ACTIVE)

    async def revert(self, ident: str, *, token: str) -> TuningRow:
        """Take a knob back out. The row stays, with its reason and its metrics."""
        row = await self.row(ident)
        _checked(row, token, _REVERT_FROM, TuningStatus.REVERTED)
        return await self._move(row, TuningStatus.REVERTED)

    async def record(
        self, tuning_id: int, metrics: TuningMetrics, status: TuningStatus
    ) -> TuningRow:
        """Write one trial's measured metrics and the status its verdict earns (spec 11).

        A passing trial leaves the row `pending`, which is the only status a human may accept; a
        guard that refused lands it `rejected`, which is what stops the loop measuring it again.
        """
        await self.index.tuning.set_tuning_metrics(tuning_id, metrics, status)
        return await self.row(str(tuning_id))

    async def _move(self, row: TuningRow, status: TuningStatus) -> TuningRow:
        await self.index.tuning.set_tuning_status(row.tuning_id, status)
        return await self.row(str(row.tuning_id))


def _judged(row: TuningRow) -> None:
    """Refuse a row no trial measured, and one a spec 11 guard refused, each by its own name."""
    if row.metrics.refused:
        raise TuningRefused(
            f"tuning {row.tuning_id} failed a spec 11 guard: {row.metrics.refused}"
        )
    if not row.metrics.measured_at:
        raise TuningRefused(
            f"tuning {row.tuning_id} has no trial yet; run `auditr graph tuning measure "
            f"{row.tuning_id}` or let the observer measure it"
        )


def _checked(
    row: TuningRow, token: str, allowed: frozenset[TuningStatus], to: TuningStatus
) -> None:
    """The two things every hand transition shares: a legal start, and the confirmation word."""
    if row.status not in allowed:
        raise TuningRefused(
            f"tuning {row.tuning_id} is {row.status.value}; only "
            f"{sorted(s.value for s in allowed)} can become {to.value}"
        )
    if token != row.token:
        raise TuningRefused(
            f"wrong confirmation word for tuning {row.tuning_id}; "
            f"`auditr graph tuning list` prints it"
        )


def check_cap(active: Sequence[TuningRow], cap: int) -> None:
    """`stopwords_max` bounds the stopword rows a build reads, at propose time and at accept time."""
    live = [r for r in active if r.key == "stopwords"]
    if len(live) >= cap:
        raise TuningRefused(
            f"{len(live)} stopwords are already active and the cap is {cap}; revert one first"
        )
