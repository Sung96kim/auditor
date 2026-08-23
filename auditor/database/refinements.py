"""The identity-keyed stores: ``RunsDB``, ``RefinementsDB``, ``TuningDB`` and ``EvalsDB``.

Keyed by ``repo_identity`` rather than by the repo partition, so a forgotten partition never takes
another worktree's work with it, and preserved across a ``SCHEMA_VERSION`` bump. They share this
module because they share the row decoders and the ``IN`` clause helper below.
"""

import json
import sqlite3
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Column, Index, Table
from auditor.graph.refine.models import (
    ACTIVE_STATUSES,
    Anchor,
    EvalMetrics,
    EvalRow,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
    Run,
    RunnerKind,
    RunOutcome,
    RunStatus,
    RunUsage,
    TuningRow,
    TuningStatus,
)

_DAY_SECONDS = 86_400


def _run_from_row(row: sqlite3.Row) -> Run:
    """One row as a `Run`, rebuilding the sub-models the insert spread into flat columns."""
    data = dict(row)
    data["trigger_detail"] = json.loads(data["trigger_detail"])
    data["tool_trace"] = json.loads(data["tool_trace"])
    data["usage"] = {field: data.pop(field) for field in RunUsage.model_fields}
    return Run.model_validate(data)


def _refinement_from_row(row: sqlite3.Row) -> Refinement:
    data = dict(row)
    for column in ("target", "payload", "evidence"):
        data[column] = json.loads(data[column])
    return Refinement.model_validate(data)


def _usage_values(usage: RunUsage) -> dict[str, Any]:
    """The usage columns; SQLite has no boolean, so the estimated flag lands as 0 or 1."""
    return {**usage.model_dump(), "cost_estimated": int(usage.cost_estimated)}


def _run_values(run: Run) -> dict[str, Any]:
    """One run as a column name -> value mapping, so the insert cannot transpose two columns."""
    return {
        "run_id": run.run_id,
        "repo_identity": run.repo_identity,
        "origin_partition": run.origin_partition,
        "partition_prefix": run.partition_prefix,
        "client": run.client.value,
        "producer": run.producer.value,
        "runner": run.runner.value,
        "trigger_kind": run.trigger_kind.value,
        "trigger_detail": run.trigger_detail.model_dump_json(),
        "session_id": run.session_id,
        "agent_name": run.agent_name,
        "branch": run.branch,
        "commit_sha": run.commit_sha,
        "dirty": int(run.dirty),
        "model": run.model,
        "prompt": run.prompt,
        "system_prompt_sha": run.system_prompt_sha,
        "tool_trace": json.dumps([call.model_dump() for call in run.tool_trace]),
        **_usage_values(run.usage),
        "sdk_session_id": run.sdk_session_id,
        "status": run.status.value,
        "summary": run.summary,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _outcome_values(outcome: RunOutcome, *, now: float) -> dict[str, Any]:
    """A terminal state as a column name -> value mapping, one key per ``RunOutcome`` field."""
    values = {field: getattr(outcome, field) for field in RunOutcome.model_fields}
    values.pop("usage")
    values["status"] = outcome.status.value
    values["tool_trace"] = json.dumps(
        [call.model_dump() for call in outcome.tool_trace]
    )
    values["finished_at"] = outcome.finished_at if outcome.finished_at else now
    return values | _usage_values(outcome.usage)


def _refinement_values(refinement: Refinement) -> dict[str, Any]:
    return {
        "run_id": refinement.run_id,
        "repo_identity": refinement.repo_identity,
        "kind": refinement.kind.value,
        "target": refinement.target.model_dump_json(exclude_defaults=True),
        "payload": refinement.payload.model_dump_json(exclude_defaults=True),
        "reason": refinement.reason,
        "evidence": json.dumps([e.model_dump() for e in refinement.evidence]),
        "confidence": refinement.confidence,
        "tier": refinement.tier.value,
        "status": refinement.status.value,
        "drifted": int(refinement.drifted),
        "noop_builds": refinement.noop_builds,
        "supersedes": refinement.supersedes,
        "attempts": refinement.attempts,
        "created_at": refinement.created_at,
        "status_at": refinement.status_at,
    }


def _eval_from_row(row: sqlite3.Row) -> EvalRow:
    """One row as an `EvalRow`, rebuilding the metrics block from its flat columns."""
    data = dict(row)
    data["metrics"] = {field: data.pop(field) for field in EvalMetrics.model_fields}
    return EvalRow.model_validate(data)


def _in_clause(column: str, values: Sequence[object]) -> str:
    return f" AND {column} IN ({','.join('?' for _ in values)})"


class RunsDB(BaseDB):
    """Table store for ``graph_runs``: one row per decision, model call or not (spec 5.3).

    Reads bind the handle's identity; writes address a globally unique id and bind the identity
    too, so neither can reach another checkout's rows.
    """

    attr: ClassVar[str] = "runs"
    TABLES: ClassVar[dict[str, Table]] = {
        "graph_runs": Table(
            repo_fk=False,
            cache=False,
            cols=(
                Column(name="run_id", type="TEXT", not_null=True, primary_key=True),
                Column(name="repo_identity", type="TEXT", not_null=True),
                Column(
                    name="origin_partition", type="TEXT", not_null=True, default="''"
                ),
                Column(
                    name="partition_prefix", type="TEXT", not_null=True, default="''"
                ),
                Column(name="client", type="TEXT", not_null=True, default="'cli'"),
                Column(name="producer", type="TEXT", not_null=True, default="'cli'"),
                Column(name="runner", type="TEXT", not_null=True, default="'none'"),
                Column(
                    name="trigger_kind", type="TEXT", not_null=True, default="'manual'"
                ),
                Column(
                    name="trigger_detail", type="TEXT", not_null=True, default="'{}'"
                ),
                Column(name="session_id", type="TEXT"),
                Column(name="agent_name", type="TEXT"),
                Column(name="branch", type="TEXT"),
                Column(name="commit_sha", type="TEXT"),
                Column(name="dirty", type="INTEGER", not_null=True, default="0"),
                Column(name="model", type="TEXT"),
                Column(name="prompt", type="TEXT"),
                Column(name="system_prompt_sha", type="TEXT"),
                Column(name="tool_trace", type="TEXT", not_null=True, default="'[]'"),
                Column(name="cost_usd", type="REAL", not_null=True, default="0"),
                Column(
                    name="cost_estimated", type="INTEGER", not_null=True, default="0"
                ),
                Column(name="input_tokens", type="INTEGER", not_null=True, default="0"),
                Column(
                    name="output_tokens", type="INTEGER", not_null=True, default="0"
                ),
                Column(name="num_turns", type="INTEGER", not_null=True, default="0"),
                Column(name="sdk_session_id", type="TEXT"),
                Column(name="status", type="TEXT", not_null=True, default="'queued'"),
                Column(name="summary", type="TEXT"),
                Column(name="error", type="TEXT"),
                Column(name="started_at", type="REAL", not_null=True, default="0"),
                Column(name="finished_at", type="REAL"),
            ),
            indexes=(
                Index(
                    name="graph_runs_identity", columns=("repo_identity", "started_at")
                ),
            ),
        ),
    }

    async def add_run(self, run: Run) -> str:
        sql, binds = self.insert_sql("graph_runs", _run_values(run))

        def op(conn: sqlite3.Connection) -> str:
            conn.execute(sql, binds)
            conn.commit()
            return run.run_id

        return await self._worker.run(op)

    async def finish_run(self, run_id: str, outcome: RunOutcome) -> None:
        """Stamp a run's terminal state. Cost is recorded even for `aborted` (spec 5.3)."""
        values = _outcome_values(outcome, now=time.time())
        assignments = ", ".join(f"{column}=?" for column in values)
        sql = (
            f"UPDATE graph_runs SET {assignments} "  # noqa: S608  (columns come from RunOutcome)
            "WHERE run_id=? AND repo_identity=?"
        )
        binds = (*values.values(), run_id, self.partition.identity)

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(sql, binds)
            conn.commit()

        await self._worker.run(op)

    async def run(self, run_id: str) -> Run | None:
        row = await self._fetch_one_by_identity(
            "SELECT * FROM graph_runs WHERE repo_identity = ? AND run_id = ?", (run_id,)
        )
        return _run_from_row(row) if row else None

    async def runs(
        self,
        *,
        statuses: Sequence[RunStatus] | None = None,
        limit: int | None = None,
    ) -> list[Run]:
        """Runs newest first. Both filters run in SQL, so ``limit`` counts rows the caller sees."""
        sql = "SELECT * FROM graph_runs WHERE repo_identity = ?"
        params: list[Any] = []
        if statuses:
            sql += _in_clause("status", statuses)
            params += [s.value for s in statuses]
        sql += " ORDER BY started_at DESC, run_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [
            _run_from_row(r) for r in await self._fetch_by_identity(sql, tuple(params))
        ]

    async def prune_skipped_runs(
        self, retention_days: int, *, now: float | None = None
    ) -> int:
        """Drop this identity's assessment-only rows older than ``retention_days`` (spec 5.1).

        ``retention_days`` is the daemon's ``ObserverConfig.skipped_retention_days``. Real runs are
        kept forever, and so is a skipped run owning a refinement or a tuning row: the guard reads
        both child tables, which belong to the two stores below.
        """
        cutoff = (time.time() if now is None else now) - retention_days * _DAY_SECONDS
        identity = self.partition.identity

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "DELETE FROM graph_runs WHERE repo_identity = ? AND status = ? "
                "AND started_at < ? "
                "AND run_id NOT IN (SELECT run_id FROM graph_refinements WHERE repo_identity = ?) "
                "AND run_id NOT IN (SELECT run_id FROM graph_tuning WHERE repo_identity = ?)",
                (identity, RunStatus.SKIPPED.value, cutoff, identity, identity),
            )
            conn.commit()
            return cur.rowcount

        return await self._worker.run(op)


class RefinementsDB(BaseDB):
    """Table store for ``graph_refinements`` and ``graph_refinement_anchors`` (spec 5.4, 5.5).

    Reads bind the handle's identity; writes address a globally unique id and bind the identity
    too, so neither can reach another checkout's rows.
    """

    attr: ClassVar[str] = "refinements"
    TABLES: ClassVar[dict[str, Table]] = {
        "graph_refinements": Table(
            repo_fk=False,
            cache=False,
            cols=(
                Column(
                    name="refinement_id",
                    type="INTEGER",
                    primary_key=True,
                    autoincrement=True,
                ),
                Column(
                    name="run_id",
                    type="TEXT",
                    not_null=True,
                    references="graph_runs (run_id)",
                ),
                Column(name="repo_identity", type="TEXT", not_null=True),
                Column(name="kind", type="TEXT", not_null=True),
                Column(name="target", type="TEXT", not_null=True, default="'{}'"),
                Column(name="payload", type="TEXT", not_null=True, default="'{}'"),
                Column(name="reason", type="TEXT", not_null=True, default="''"),
                Column(name="evidence", type="TEXT", not_null=True, default="'[]'"),
                Column(name="confidence", type="REAL", not_null=True, default="0"),
                Column(name="tier", type="TEXT", not_null=True, default="'C'"),
                Column(name="status", type="TEXT", not_null=True, default="'pending'"),
                Column(name="drifted", type="INTEGER", not_null=True, default="0"),
                Column(name="noop_builds", type="INTEGER", not_null=True, default="0"),
                Column(name="supersedes", type="INTEGER"),
                Column(name="attempts", type="INTEGER", not_null=True, default="0"),
                Column(name="created_at", type="REAL", not_null=True, default="0"),
                Column(name="status_at", type="REAL", not_null=True, default="0"),
            ),
            indexes=(
                Index(
                    name="graph_refinements_identity",
                    columns=("repo_identity", "status"),
                ),
            ),
        ),
        "graph_refinement_anchors": Table(
            repo_fk=False,
            cache=False,
            cols=(
                Column(
                    name="refinement_id",
                    type="INTEGER",
                    not_null=True,
                    primary_key=True,
                    references="graph_refinements (refinement_id) ON DELETE CASCADE",
                ),
                Column(name="node_id", type="TEXT", not_null=True, primary_key=True),
                Column(name="path", type="TEXT", not_null=True),
                Column(name="truth_sha", type="TEXT", not_null=True),
                Column(name="file_sha", type="TEXT", not_null=True, default="''"),
            ),
            indexes=(
                Index(name="graph_anchors_refinement", columns=("refinement_id",)),
            ),
        ),
    }

    async def add_refinement(
        self, refinement: Refinement, anchors: Sequence[Anchor] = ()
    ) -> int:
        """Insert one refinement and its anchors together; returns the assigned id."""
        sql, binds = self.insert_sql(
            "graph_refinements", _refinement_values(refinement)
        )

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(sql, binds)
            new_id = int(cur.lastrowid)
            anchor_sql, anchor_binds = self.insert_many_sql(
                "graph_refinement_anchors",
                [
                    {
                        "refinement_id": new_id,
                        "node_id": a.node_id,
                        "path": a.path,
                        "truth_sha": a.truth_sha,
                        "file_sha": a.file_sha,
                    }
                    for a in anchors
                ],
                or_replace=True,
            )
            conn.executemany(anchor_sql, anchor_binds)
            conn.commit()
            return new_id

        return await self._worker.run(op)

    async def refinements(
        self,
        *,
        statuses: Sequence[RefinementStatus] | None = None,
        kinds: Sequence[RefinementKind] | None = None,
        limit: int | None = None,
    ) -> list[Refinement]:
        sql = "SELECT * FROM graph_refinements WHERE repo_identity = ?"
        params: list[Any] = []
        if statuses:
            sql += _in_clause("status", statuses)
            params += [s.value for s in statuses]
        if kinds:
            sql += _in_clause("kind", kinds)
            params += [k.value for k in kinds]
        sql += " ORDER BY refinement_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [
            _refinement_from_row(r)
            for r in await self._fetch_by_identity(sql, tuple(params))
        ]

    async def active(self) -> list[Refinement]:
        """The refinements a build applies: `active` plus `pinned` (spec 5.7)."""
        return await self.refinements(statuses=sorted(ACTIVE_STATUSES))

    async def anchors(
        self, refinement_ids: Sequence[int]
    ) -> dict[int, tuple[Anchor, ...]]:
        """The anchors of ``refinement_ids`` that belong to this identity, by refinement id."""
        if not refinement_ids:
            return {}
        placeholders = ",".join("?" for _ in refinement_ids)
        # the anchor rows carry no identity of their own, so they are scoped through their parent
        rows = await self._fetch_by_identity(
            "SELECT * FROM graph_refinement_anchors WHERE refinement_id IN "
            "(SELECT refinement_id FROM graph_refinements WHERE repo_identity = ?) "
            f"AND refinement_id IN ({placeholders}) "  # noqa: S608  (placeholders only)
            "ORDER BY refinement_id, node_id",
            tuple(refinement_ids),
        )
        out: dict[int, list[Anchor]] = {}
        for row in rows:
            anchor = Anchor.model_validate(dict(row))
            out.setdefault(anchor.refinement_id, []).append(anchor)
        return {rid: tuple(anchors) for rid, anchors in out.items()}

    async def set_status(
        self, refinement_id: int, status: RefinementStatus, *, now: float | None = None
    ) -> None:
        stamp = time.time() if now is None else now
        identity = self.partition.identity

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE graph_refinements SET status=?, status_at=? "
                "WHERE refinement_id=? AND repo_identity=?",
                (status.value, stamp, refinement_id, identity),
            )
            conn.commit()

        await self._worker.run(op)

    def write_outcomes(
        self,
        conn: sqlite3.Connection,
        outcomes: Sequence[RefinementOutcome],
        now: float,
    ) -> None:
        """Apply one build's verdicts on the open connection, without committing.

        The build's transaction owns the commit, so a graph and its provenance land together;
        S4b's ``_persist`` is the caller that needs it.
        """
        identity = self.partition.identity
        for outcome in outcomes:
            conn.execute(
                "UPDATE graph_refinements SET noop_builds=?, drifted=? "
                "WHERE refinement_id=? AND repo_identity=?",
                (
                    outcome.noop_builds,
                    int(outcome.drifted),
                    outcome.refinement_id,
                    identity,
                ),
            )
            if outcome.status is not None:
                conn.execute(
                    "UPDATE graph_refinements SET status=?, status_at=? "
                    "WHERE refinement_id=? AND repo_identity=?",
                    (outcome.status.value, now, outcome.refinement_id, identity),
                )

    async def apply_outcomes(
        self, outcomes: Sequence[RefinementOutcome], *, now: float | None = None
    ) -> None:
        stamp = time.time() if now is None else now

        def op(conn: sqlite3.Connection) -> None:
            self.write_outcomes(conn, outcomes, stamp)
            conn.commit()

        await self._worker.run(op)


class TuningDB(BaseDB):
    """Table store for ``graph_tuning``: the proposed knob changes (spec 5.8)."""

    attr: ClassVar[str] = "tuning"
    TABLES: ClassVar[dict[str, Table]] = {
        "graph_tuning": Table(
            repo_fk=False,
            cache=False,
            cols=(
                Column(
                    name="tuning_id",
                    type="INTEGER",
                    primary_key=True,
                    autoincrement=True,
                ),
                Column(name="repo_identity", type="TEXT", not_null=True),
                Column(name="key", type="TEXT", not_null=True),
                Column(name="value_json", type="TEXT", not_null=True),
                Column(name="token", type="TEXT", not_null=True, default="''"),
                Column(
                    name="run_id",
                    type="TEXT",
                    not_null=True,
                    references="graph_runs (run_id)",
                ),
                Column(name="reason", type="TEXT", not_null=True, default="''"),
                Column(name="status", type="TEXT", not_null=True, default="'pending'"),
                Column(name="metrics", type="TEXT", not_null=True, default="'{}'"),
                Column(name="created_at", type="REAL", not_null=True, default="0"),
            ),
            indexes=(
                Index(
                    name="graph_tuning_identity", columns=("repo_identity", "status")
                ),
            ),
        ),
    }

    async def add_tuning(self, row: TuningRow) -> int:
        sql, binds = self.insert_sql(
            "graph_tuning",
            {
                "repo_identity": row.repo_identity,
                "key": row.key,
                "value_json": row.value_json,
                "token": row.token,
                "run_id": row.run_id,
                "reason": row.reason,
                "status": row.status.value,
                "metrics": row.metrics.model_dump_json(),
                "created_at": row.created_at,
            },
        )

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(sql, binds)
            conn.commit()
            return int(cur.lastrowid)

        return await self._worker.run(op)

    async def tuning(
        self, *, statuses: Sequence[TuningStatus] | None = None
    ) -> list[TuningRow]:
        sql = "SELECT * FROM graph_tuning WHERE repo_identity = ?"
        params: list[Any] = []
        if statuses:
            sql += _in_clause("status", statuses)
            params += [s.value for s in statuses]
        sql += " ORDER BY tuning_id"
        return [
            TuningRow.model_validate(dict(r) | {"metrics": json.loads(r["metrics"])})
            for r in await self._fetch_by_identity(sql, tuple(params))
        ]

    async def set_tuning_status(self, tuning_id: int, status: TuningStatus) -> None:
        identity = self.partition.identity

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE graph_tuning SET status=? WHERE tuning_id=? AND repo_identity=?",
                (status.value, tuning_id, identity),
            )
            conn.commit()

        await self._worker.run(op)


class EvalsDB(BaseDB):
    """Table store for ``graph_evals``: one suite stratum's measured accuracy (spec 5.8, 10.2)."""

    attr: ClassVar[str] = "evals"
    TABLES: ClassVar[dict[str, Table]] = {
        "graph_evals": Table(
            repo_fk=False,
            cache=False,
            cols=(
                Column(
                    name="eval_id", type="INTEGER", primary_key=True, autoincrement=True
                ),
                Column(name="repo_identity", type="TEXT", not_null=True),
                Column(name="runner", type="TEXT", not_null=True),
                Column(name="model", type="TEXT", not_null=True),
                Column(name="suite", type="TEXT", not_null=True),
                Column(name="stratum", type="TEXT", not_null=True),
                Column(name="n", type="INTEGER", not_null=True, default="0"),
                Column(name="correct", type="INTEGER", not_null=True, default="0"),
                Column(name="precision", type="REAL", not_null=True, default="0"),
                Column(name="recall", type="REAL", not_null=True, default="0"),
                Column(name="false_add_rate", type="REAL", not_null=True, default="0"),
                Column(
                    name="false_removal_rate", type="REAL", not_null=True, default="0"
                ),
                Column(name="lower_bound_95", type="REAL", not_null=True, default="0"),
                Column(name="cost_usd", type="REAL", not_null=True, default="0"),
                Column(name="num_turns", type="INTEGER", not_null=True, default="0"),
                Column(name="created_at", type="REAL", not_null=True, default="0"),
            ),
            indexes=(
                Index(
                    name="graph_evals_identity",
                    columns=("repo_identity", "runner", "model"),
                ),
            ),
        ),
    }

    async def add_eval(self, row: EvalRow) -> int:
        sql, binds = self.insert_sql(
            "graph_evals",
            {
                "repo_identity": row.repo_identity,
                "runner": row.runner.value,
                "model": row.model,
                "suite": row.suite,
                "stratum": row.stratum,
                **row.metrics.model_dump(),
                "cost_usd": row.cost_usd,
                "num_turns": row.num_turns,
                "created_at": row.created_at,
            },
        )

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(sql, binds)
            conn.commit()
            return int(cur.lastrowid)

        return await self._worker.run(op)

    async def evals(
        self, *, runner: RunnerKind | None = None, model: str | None = None
    ) -> list[EvalRow]:
        """Eval rows for this identity, newest last. S6's tier gate reads them per stratum."""
        sql = "SELECT * FROM graph_evals WHERE repo_identity = ?"
        params: list[Any] = []
        if runner is not None:
            sql += " AND runner = ?"
            params.append(runner.value)
        if model is not None:
            sql += " AND model = ?"
            params.append(model)
        sql += " ORDER BY eval_id"
        return [
            _eval_from_row(r) for r in await self._fetch_by_identity(sql, tuple(params))
        ]
