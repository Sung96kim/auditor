"""GraphDB: the ``graph_*`` table store. Cached per-file facts, the materialized nodes, edges
and clusters, and the unresolved queue."""

# auditor: skip-file: PY-OOP-PARALLEL-SIBLING  (data-access layer: each read method is a thin
# delegation to the shared _fetch helper differing only in its SQL — parallel shape is the query
# surface, not duplication; the substantive body was already extracted, clearing TWIN-METHODS)

import json
import sqlite3
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Column, Index, Table
from auditor.graph.hashes import FileHashes
from auditor.graph.model import GraphCluster, GraphEdge, GraphNode, UnresolvedRow


def _decode_unresolved(row: sqlite3.Row) -> dict[str, Any]:
    """One queue row as a payload dict: JSON columns decoded, the flag a bool, ``repo`` dropped."""
    out = dict(row)
    out.pop("repo", None)
    for col in ("candidates", "definers", "resolution_path"):
        out[col] = json.loads(out.pop(f"{col}_json"))
    out["externally_bound"] = bool(out["externally_bound"])
    return out


class GraphDB(BaseDB):
    """Table store for the ``graph_*`` tables: the cached per-file facts, the materialized nodes,
    edges and clusters, and the unresolved queue. ``TABLES`` below is the list that does not rot."""

    attr: ClassVar[str] = "graph"
    TABLES: ClassVar[dict[str, Table]] = {
        "graph_facts": Table(
            cols=(
                Column(name="path", type="TEXT", not_null=True, primary_key=True),
                Column(name="facts_json", type="TEXT", not_null=True),
                Column(name="content_hash", type="TEXT", not_null=True),
                Column(name="truth_sha", type="TEXT"),
                Column(name="facts_sha", type="TEXT"),
            ),
        ),
        "graph_nodes": Table(
            cols=(
                Column(name="node_id", type="TEXT", not_null=True, primary_key=True),
                Column(name="kind", type="TEXT", not_null=True),
                Column(name="name", type="TEXT", not_null=True),
                Column(name="module", type="TEXT", not_null=True),
                Column(name="role", type="TEXT", not_null=True),
                Column(name="line", type="INTEGER", not_null=True),
                Column(name="rank", type="REAL", not_null=True, default="0"),
                Column(name="cluster_id", type="INTEGER"),
                Column(name="abstractness", type="REAL", not_null=True, default="0"),
                Column(name="text_sparse", type="INTEGER", not_null=True, default="0"),
                Column(name="refined", type="INTEGER", not_null=True, default="0"),
                Column(name="annotation", type="TEXT"),
            ),
            indexes=(
                Index(name="graph_nodes_cluster", columns=("repo", "cluster_id")),
            ),
        ),
        "graph_edges": Table(
            cols=(
                Column(name="src", type="TEXT", not_null=True),
                Column(name="dst", type="TEXT", not_null=True),
                Column(name="kind", type="TEXT", not_null=True),
                Column(name="weight", type="REAL", not_null=True, default="1"),
                Column(
                    name="source",
                    type="TEXT",
                    not_null=True,
                    default="'deterministic'",
                ),
                Column(name="confirmed", type="INTEGER", not_null=True, default="0"),
            ),
            indexes=(
                Index(name="graph_edges_src", columns=("repo", "src")),
                Index(name="graph_edges_dst", columns=("repo", "dst")),
                Index(
                    name="graph_edges_key",
                    columns=("repo", "src", "dst", "kind"),
                    unique=True,
                ),
            ),
        ),
        "graph_clusters": Table(
            cols=(
                Column(
                    name="cluster_id", type="INTEGER", not_null=True, primary_key=True
                ),
                Column(name="label", type="TEXT", not_null=True),
                Column(name="member_count", type="INTEGER", not_null=True),
                Column(
                    name="label_source",
                    type="TEXT",
                    not_null=True,
                    default="'deterministic'",
                ),
            ),
        ),
        "graph_unresolved": Table(
            cols=(
                Column(name="node_id", type="TEXT", not_null=True, primary_key=True),
                Column(name="name", type="TEXT", not_null=True, primary_key=True),
                Column(name="reason", type="TEXT", not_null=True, primary_key=True),
                Column(name="fact_kind", type="TEXT", not_null=True),
                Column(name="receiver_root", type="TEXT"),
                Column(name="call_form", type="TEXT", not_null=True, default="'bare'"),
                Column(
                    name="candidates_json", type="TEXT", not_null=True, default="'[]'"
                ),
                Column(
                    name="definers_json", type="TEXT", not_null=True, default="'[]'"
                ),
                Column(
                    name="resolution_path_json",
                    type="TEXT",
                    not_null=True,
                    default="'[]'",
                ),
                Column(name="priority", type="INTEGER", not_null=True, default="4"),
                Column(
                    name="externally_bound", type="INTEGER", not_null=True, default="0"
                ),
            ),
            indexes=(
                Index(name="graph_unresolved_priority", columns=("repo", "priority")),
                Index(name="graph_unresolved_reason", columns=("repo", "reason")),
            ),
        ),
    }

    async def set_facts(
        self,
        path: str,
        facts_json: str,
        content_hash: str,
        hashes: FileHashes | None = None,
    ) -> None:
        """Cache one file's facts. ``hashes`` is the spec 5.5 pair, which the assessment compares
        against a re-extraction; callers without parsed facts leave it out and store NULLs."""

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            conn.execute(
                "INSERT INTO graph_facts (repo, path, facts_json, content_hash, truth_sha, facts_sha) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(repo, path) DO UPDATE SET "
                "facts_json=excluded.facts_json, content_hash=excluded.content_hash, "
                "truth_sha=excluded.truth_sha, facts_sha=excluded.facts_sha",
                (
                    self.repo,
                    path,
                    facts_json,
                    content_hash,
                    hashes.truth if hashes else None,
                    hashes.facts if hashes else None,
                ),
            )
            conn.commit()

        await self._worker.run(op)

    async def clear_facts(self) -> None:
        """Drop all cached per-file facts for this repo, forcing re-extraction on the next scan
        (facts are keyed by content hash, so a code change to the extractor needs this)."""

        def op(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM graph_facts WHERE repo = ?", (self.repo,))
            conn.commit()

        await self._worker.run(op)

    async def facts_hash(self, path: str) -> str | None:
        row = await self._fetch_one(
            "SELECT content_hash FROM graph_facts WHERE repo = ? AND path = ?", (path,)
        )
        return row["content_hash"] if row else None

    async def all_facts(self) -> list[str]:
        rows = await self._fetch(
            "SELECT facts_json FROM graph_facts WHERE repo = ? ORDER BY path"
        )
        return [r["facts_json"] for r in rows]

    async def facts(self, path: str) -> str | None:
        """The cached ``FileGraphFacts`` JSON for one file, or ``None`` when it isn't indexed."""
        row = await self._fetch_one(
            "SELECT facts_json FROM graph_facts WHERE repo = ? AND path = ?", (path,)
        )
        return row["facts_json"] if row else None

    async def hashes(self, path: str) -> FileHashes | None:
        """The cached spec 5.5 pair for one file, or ``None`` when it was written without them."""
        row = await self._fetch_one(
            "SELECT truth_sha, facts_sha FROM graph_facts WHERE repo = ? AND path = ?",
            (path,),
        )
        if row is None or row["truth_sha"] is None:
            return None
        return FileHashes(truth=row["truth_sha"], facts=row["facts_sha"])

    def write_graph(
        self,
        conn: sqlite3.Connection,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        clusters: list[GraphCluster],
    ) -> None:
        """Swap this repo's graph on an open connection, without committing."""
        node_rows = [
            (
                self.repo,
                n.id,
                n.kind.value,
                n.name,
                n.module,
                n.role,
                n.line,
                n.rank,
                n.cluster_id,
                n.abstractness,
                int(n.text_sparse),
                int(n.refined),
                n.annotation,
            )
            for n in nodes
        ]
        edge_rows = [
            (
                self.repo,
                e.src,
                e.dst,
                e.kind.value,
                e.weight,
                e.source.value,
                int(e.confirmed),
            )
            for e in edges
        ]
        clu_rows = [
            (self.repo, c.cluster_id, c.label, c.member_count, c.label_source.value)
            for c in clusters
        ]

        self._ensure_repo(conn)
        for t in ("graph_nodes", "graph_edges", "graph_clusters"):
            conn.execute(f"DELETE FROM {t} WHERE repo = ?", (self.repo,))  # noqa: S608
        conn.executemany(
            "INSERT OR REPLACE INTO graph_nodes (repo, node_id, kind, name, module, "
            "role, line, rank, cluster_id, abstractness, text_sparse, refined, annotation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            node_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO graph_edges (repo, src, dst, kind, weight, source, confirmed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            edge_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO graph_clusters (repo, cluster_id, label, member_count, label_source) "
            "VALUES (?, ?, ?, ?, ?)",
            clu_rows,
        )

    async def replace(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        clusters: list[GraphCluster],
    ) -> None:
        def op(conn: sqlite3.Connection) -> None:
            self.write_graph(conn, nodes, edges, clusters)
            conn.commit()

        await self._worker.run(op)

    async def node(self, node_id: str) -> dict[str, Any] | None:
        row = await self._fetch_one(
            "SELECT * FROM graph_nodes WHERE repo = ? AND node_id = ?", (node_id,)
        )
        return dict(row) if row else None

    async def nodes(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in await self._fetch(
                "SELECT * FROM graph_nodes WHERE repo = ? ORDER BY rank DESC, node_id"
            )
        ]

    async def edges_of(
        self, node_id: str, kinds: list[str] | None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT src, dst, kind, weight, source, confirmed FROM graph_edges "
            "WHERE repo = ? AND (src = ? OR dst = ?)"
        )
        params: list[Any] = [node_id, node_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params += kinds
        # same ORDER BY as all_edges, so a caller reading one node sees the whole-partition order
        sql += " ORDER BY src, dst, kind"
        return [dict(r) for r in await self._fetch(sql, tuple(params))]

    async def cluster_members(self, cluster_id: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in await self._fetch(
                "SELECT node_id AS id, name, module, rank, refined, annotation FROM graph_nodes "
                "WHERE repo = ? AND cluster_id = ? ORDER BY rank DESC, node_id",
                (cluster_id,),
            )
        ]

    async def clusters(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in await self._fetch(
                "SELECT cluster_id, label, member_count, label_source FROM graph_clusters "
                "WHERE repo = ? ORDER BY member_count DESC, cluster_id"
            )
        ]

    async def all_edges(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in await self._fetch(
                "SELECT src, dst, kind, weight, source, confirmed FROM graph_edges "
                "WHERE repo = ? ORDER BY src, dst, kind"
            )
        ]

    def write_unresolved(
        self, conn: sqlite3.Connection, rows: list[UnresolvedRow]
    ) -> None:
        """Swap this repo's whole unresolved queue on an open connection, without committing."""
        values = [
            (
                self.repo,
                r.node_id,
                r.name,
                r.reason.value,
                r.fact_kind.value,
                r.receiver_root,
                r.call_form.value,
                json.dumps(list(r.candidates)),
                json.dumps(list(r.definers)),
                json.dumps(list(r.resolution_path)),
                r.priority,
                int(r.externally_bound),
            )
            for r in rows
        ]
        self._ensure_repo(conn)
        conn.execute("DELETE FROM graph_unresolved WHERE repo = ?", (self.repo,))
        conn.executemany(
            "INSERT OR REPLACE INTO graph_unresolved (repo, node_id, name, reason, "
            "fact_kind, receiver_root, call_form, candidates_json, definers_json, "
            "resolution_path_json, priority, externally_bound) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )

    async def replace_unresolved(self, rows: list[UnresolvedRow]) -> None:
        """Swap this repo's whole unresolved queue for ``rows``; every build rebuilds it."""

        def op(conn: sqlite3.Connection) -> None:
            self.write_unresolved(conn, rows)
            conn.commit()

        await self._worker.run(op)

    async def unresolved(
        self,
        node_ids: list[str] | None = None,
        reasons: list[str] | None = None,
        call_forms: list[str] | None = None,
        limit: int | None = None,
        external: bool = True,
    ) -> list[dict[str, Any]]:
        """Queue rows in drain order: priority, then the externally bound rows, then node id and
        name. Every filter and the limit run in SQL, so the limit counts rows the caller sees."""
        sql = "SELECT * FROM graph_unresolved WHERE repo = ?"
        params: list[Any] = []
        for col, values in (
            ("node_id", node_ids),
            ("reason", reasons),
            ("call_form", call_forms),
        ):
            if values:
                sql += f" AND {col} IN ({','.join('?' for _ in values)})"
                params += values
        if not external:
            sql += " AND externally_bound = 0"
        sql += " ORDER BY priority, externally_bound, node_id, name"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_decode_unresolved(r) for r in await self._fetch(sql, tuple(params))]
