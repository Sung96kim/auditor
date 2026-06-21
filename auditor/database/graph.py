"""GraphDB: table store for graph_facts, graph_nodes, graph_edges, and graph_clusters tables."""

import sqlite3
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Column, Index, Table
from auditor.graph.model import GraphCluster, GraphEdge, GraphNode


class GraphDB(BaseDB):
    """Table store for the ``graph_facts``, ``graph_nodes``, ``graph_edges``, and
    ``graph_clusters`` tables."""

    attr: ClassVar[str] = "graph"
    TABLES: ClassVar[dict[str, Table]] = {
        "graph_facts": Table(
            cols=(
                Column(name="path", type="TEXT", not_null=True, primary_key=True),
                Column(name="facts_json", type="TEXT", not_null=True),
                Column(name="content_hash", type="TEXT", not_null=True),
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
            ),
            indexes=(
                Index(name="graph_edges_src", columns=("repo", "src")),
                Index(name="graph_edges_dst", columns=("repo", "dst")),
            ),
        ),
        "graph_clusters": Table(
            cols=(
                Column(
                    name="cluster_id", type="INTEGER", not_null=True, primary_key=True
                ),
                Column(name="label", type="TEXT", not_null=True),
                Column(name="member_count", type="INTEGER", not_null=True),
            ),
        ),
    }

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
