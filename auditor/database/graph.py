"""GraphDB: table store for graph_facts, graph_nodes, graph_edges, and graph_clusters tables."""

import sqlite3
from typing import Any, ClassVar

from auditor.database.base import BaseDB
from auditor.graph.model import GraphCluster, GraphEdge, GraphNode


class GraphDB(BaseDB):
    """Table store for the ``graph_facts``, ``graph_nodes``, ``graph_edges``, and
    ``graph_clusters`` tables."""

    SCHEMA: ClassVar[str] = """CREATE TABLE IF NOT EXISTS graph_facts (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    path TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (repo, path)
);
CREATE TABLE IF NOT EXISTS graph_nodes (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    module TEXT NOT NULL,
    role TEXT NOT NULL,
    line INTEGER NOT NULL,
    rank REAL NOT NULL DEFAULT 0,
    cluster_id INTEGER,
    abstractness REAL NOT NULL DEFAULT 0,
    text_sparse INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, node_id)
);
CREATE TABLE IF NOT EXISTS graph_edges (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS graph_clusters (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    PRIMARY KEY (repo, cluster_id)
);
CREATE INDEX IF NOT EXISTS graph_nodes_cluster ON graph_nodes (repo, cluster_id);
CREATE INDEX IF NOT EXISTS graph_edges_src ON graph_edges (repo, src);
CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges (repo, dst);"""
    CACHE_TABLES: ClassVar[tuple[str, ...]] = (
        "graph_facts",
        "graph_edges",
        "graph_clusters",
        "graph_nodes",
    )

    async def set_facts(self, path: str, facts_json: str, content_hash: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            conn.execute(
                "INSERT INTO graph_facts (repo, path, facts_json, content_hash) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(repo, path) DO UPDATE SET "
                "facts_json=excluded.facts_json, content_hash=excluded.content_hash",
                (self.repo, path, facts_json, content_hash),
            )
            conn.commit()

        await self._worker.run(op)

    async def facts_hash(self, path: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT content_hash FROM graph_facts WHERE repo = ? AND path = ?",
                (self.repo, path),
            ).fetchone()
        )
        return row["content_hash"] if row else None

    async def all_facts(self) -> list[str]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT facts_json FROM graph_facts WHERE repo = ? ORDER BY path",
                (self.repo,),
            ).fetchall()
        )
        return [r["facts_json"] for r in rows]

    async def replace(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        clusters: list[GraphCluster],
    ) -> None:
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
            )
            for n in nodes
        ]
        edge_rows = [(self.repo, e.src, e.dst, e.kind.value, e.weight) for e in edges]
        clu_rows = [
            (self.repo, c.cluster_id, c.label, c.member_count) for c in clusters
        ]

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            for t in ("graph_nodes", "graph_edges", "graph_clusters"):
                conn.execute(f"DELETE FROM {t} WHERE repo = ?", (self.repo,))  # noqa: S608
            conn.executemany(
                "INSERT INTO graph_nodes (repo, node_id, kind, name, module, role, line, "
                "rank, cluster_id, abstractness, text_sparse) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                node_rows,
            )
            conn.executemany(
                "INSERT INTO graph_edges (repo, src, dst, kind, weight) VALUES (?, ?, ?, ?, ?)",
                edge_rows,
            )
            conn.executemany(
                "INSERT INTO graph_clusters (repo, cluster_id, label, member_count) "
                "VALUES (?, ?, ?, ?)",
                clu_rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def node(self, node_id: str) -> dict[str, Any] | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM graph_nodes WHERE repo = ? AND node_id = ?",
                (self.repo, node_id),
            ).fetchone()
        )
        return dict(row) if row else None

    async def nodes(self) -> list[dict[str, Any]]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM graph_nodes WHERE repo = ? ORDER BY rank DESC, node_id",
                (self.repo,),
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def edges_of(
        self, node_id: str, kinds: list[str] | None
    ) -> list[dict[str, Any]]:
        def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            sql = "SELECT src, dst, kind, weight FROM graph_edges WHERE repo = ? AND (src = ? OR dst = ?)"
            params: list[Any] = [self.repo, node_id, node_id]
            if kinds:
                sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
                params += kinds
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

        return await self._worker.run(op)

    async def cluster_members(self, cluster_id: int) -> list[dict[str, Any]]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT node_id AS id, name, module, rank FROM graph_nodes "
                "WHERE repo = ? AND cluster_id = ? ORDER BY rank DESC, node_id",
                (self.repo, cluster_id),
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def clusters(self) -> list[dict[str, Any]]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT cluster_id, label, member_count FROM graph_clusters "
                "WHERE repo = ? ORDER BY member_count DESC, cluster_id",
                (self.repo,),
            ).fetchall()
        )
        return [dict(r) for r in rows]
