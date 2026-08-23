"""RefinementsDB: the identity-keyed ``graph_runs`` / ``graph_refinements`` /
``graph_refinement_anchors`` tables. Keyed by ``repo_identity`` rather than by the repo partition,
so a forgotten partition never takes another worktree's work with it, and preserved across a
``SCHEMA_VERSION`` bump."""

import json
import sqlite3
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Column, Index, Table
from auditor.graph.refine.models import (
    ACTIVE_STATUSES,
    Anchor,
    EvalRow,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
    Run,
    RunnerKind,
    RunStatus,
    TuningRow,
    TuningStatus,
)

_DAY_SECONDS = 86_400


def _run_from_row(row: sqlite3.Row) -> Run:
    data = dict(row)
    data["trigger_detail"] = json.loads(data["trigger_detail"])
    data["tool_trace"] = json.loads(data["tool_trace"])
    return Run.model_validate(data)


def _refinement_from_row(row: sqlite3.Row) -> Refinement:
    data = dict(row)
    for column in ("target", "payload", "evidence"):
        data[column] = json.loads(data[column])
    return Refinement.model_validate(data)


def _in_clause(column: str, values: Sequence[object]) -> str:
    return f" AND {column} IN ({','.join('?' for _ in values)})"


class RefinementsDB(BaseDB):
    """Table store for the run, refinement and anchor tables. Reads bind the handle's identity,
    never its repo key."""

    attr: ClassVar[str] = "refinements"
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

    async def add_run(self, run: Run) -> str:
        table = self.TABLES["graph_runs"]
        columns = ", ".join(table.insert_columns())
        values = (
            run.run_id,
            run.repo_identity,
            run.origin_partition,
            run.partition_prefix,
            run.client.value,
            run.producer.value,
            run.runner.value,
            run.trigger_kind.value,
            json.dumps(run.trigger_detail),
            run.session_id,
            run.agent_name,
            run.branch,
            run.commit_sha,
            int(run.dirty),
            run.model,
            run.prompt,
            run.system_prompt_sha,
            json.dumps(run.tool_trace),
            run.cost_usd,
            int(run.cost_estimated),
            run.input_tokens,
            run.output_tokens,
            run.num_turns,
            run.sdk_session_id,
            run.status.value,
            run.summary,
            run.error,
            run.started_at,
            run.finished_at,
        )

        def op(conn: sqlite3.Connection) -> str:
            conn.execute(
                f"INSERT INTO graph_runs ({columns}) VALUES ({table.placeholders()})",  # noqa: S608
                values,
            )
            conn.commit()
            return run.run_id

        return await self._worker.run(op)

    async def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        summary: str | None = None,
        error: str | None = None,
        cost_usd: float = 0.0,
        cost_estimated: bool = False,
        input_tokens: int = 0,
        output_tokens: int = 0,
        num_turns: int = 0,
        tool_trace: Sequence[dict[str, Any]] = (),
        sdk_session_id: str | None = None,
        finished_at: float | None = None,
    ) -> None:
        """Stamp a run's terminal state. Cost is recorded even for `aborted` (spec 5.3)."""
        values = (
            status.value,
            summary,
            error,
            cost_usd,
            int(cost_estimated),
            input_tokens,
            output_tokens,
            num_turns,
            json.dumps(list(tool_trace)),
            sdk_session_id,
            finished_at if finished_at is not None else time.time(),
            run_id,
        )

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE graph_runs SET status=?, summary=?, error=?, cost_usd=?, "
                "cost_estimated=?, input_tokens=?, output_tokens=?, num_turns=?, "
                "tool_trace=?, sdk_session_id=?, finished_at=? WHERE run_id=?",
                values,
            )
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

    async def add_refinement(
        self, refinement: Refinement, anchors: Sequence[Anchor] = ()
    ) -> int:
        """Insert one refinement and its anchors together; returns the assigned id."""
        table = self.TABLES["graph_refinements"]
        columns = ", ".join(table.insert_columns())
        values = (
            refinement.run_id,
            refinement.repo_identity,
            refinement.kind.value,
            refinement.target.model_dump_json(exclude_defaults=True),
            refinement.payload.model_dump_json(exclude_defaults=True),
            refinement.reason,
            json.dumps([e.model_dump() for e in refinement.evidence]),
            refinement.confidence,
            refinement.tier.value,
            refinement.status.value,
            int(refinement.drifted),
            refinement.noop_builds,
            refinement.supersedes,
            refinement.attempts,
            refinement.created_at,
            refinement.status_at,
        )

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                f"INSERT INTO graph_refinements ({columns}) VALUES ({table.placeholders()})",  # noqa: S608
                values,
            )
            new_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT OR REPLACE INTO graph_refinement_anchors "
                "(refinement_id, node_id, path, truth_sha, file_sha) VALUES (?, ?, ?, ?, ?)",
                [(new_id, a.node_id, a.path, a.truth_sha, a.file_sha) for a in anchors],
            )
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
        if not refinement_ids:
            return {}
        placeholders = ",".join("?" for _ in refinement_ids)
        rows = await self._worker.run(
            lambda c: c.execute(
                f"SELECT * FROM graph_refinement_anchors WHERE refinement_id IN ({placeholders}) "  # noqa: S608
                "ORDER BY refinement_id, node_id",
                tuple(refinement_ids),
            ).fetchall()
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

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE graph_refinements SET status=?, status_at=? WHERE refinement_id=?",
                (status.value, stamp, refinement_id),
            )
            conn.commit()

        await self._worker.run(op)

    def write_outcomes(
        self,
        conn: sqlite3.Connection,
        outcomes: Sequence[RefinementOutcome],
        now: float,
    ) -> None:
        """Apply one build's verdicts on the open connection, without committing — the build's
        transaction owns the commit, so a graph and its provenance land together."""
        for outcome in outcomes:
            conn.execute(
                "UPDATE graph_refinements SET noop_builds=?, drifted=? WHERE refinement_id=?",
                (outcome.noop_builds, int(outcome.drifted), outcome.refinement_id),
            )
            if outcome.status is not None:
                conn.execute(
                    "UPDATE graph_refinements SET status=?, status_at=? WHERE refinement_id=?",
                    (outcome.status.value, now, outcome.refinement_id),
                )

    async def apply_outcomes(
        self, outcomes: Sequence[RefinementOutcome], *, now: float | None = None
    ) -> None:
        stamp = time.time() if now is None else now

        def op(conn: sqlite3.Connection) -> None:
            self.write_outcomes(conn, outcomes, stamp)
            conn.commit()

        await self._worker.run(op)

    async def add_tuning(self, row: TuningRow) -> int:
        table = self.TABLES["graph_tuning"]
        columns = ", ".join(table.insert_columns())
        values = (
            row.repo_identity,
            row.key,
            row.value_json,
            row.token,
            row.run_id,
            row.reason,
            row.status.value,
            json.dumps(row.metrics),
            row.created_at,
        )

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                f"INSERT INTO graph_tuning ({columns}) VALUES ({table.placeholders()})",  # noqa: S608
                values,
            )
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
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE graph_tuning SET status=? WHERE tuning_id=?",
                (status.value, tuning_id),
            )
            conn.commit()

        await self._worker.run(op)

    async def add_eval(self, row: EvalRow) -> int:
        table = self.TABLES["graph_evals"]
        columns = ", ".join(table.insert_columns())
        values = (
            row.repo_identity,
            row.runner.value,
            row.model,
            row.suite,
            row.stratum,
            row.n,
            row.correct,
            row.precision,
            row.recall,
            row.false_add_rate,
            row.false_removal_rate,
            row.lower_bound_95,
            row.cost_usd,
            row.num_turns,
            row.created_at,
        )

        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                f"INSERT INTO graph_evals ({columns}) VALUES ({table.placeholders()})",  # noqa: S608
                values,
            )
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
            EvalRow.model_validate(dict(r))
            for r in await self._fetch_by_identity(sql, tuple(params))
        ]

    async def prune_skipped_runs(
        self, retention_days: int, *, now: float | None = None
    ) -> int:
        """Drop this identity's assessment-only rows older than ``retention_days`` (spec 5.1).

        Real runs are kept forever. A skipped run that somehow owns a refinement or a tuning row is
        kept too, so the sweep can never trip a foreign key or orphan provenance.
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
