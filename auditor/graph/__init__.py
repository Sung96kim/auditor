"""Semantic codebase graph. NOTE: this package __init__ imports nothing heavier than the pydantic
model module — never the numpy/scikit-learn ones (naming/usage/rank/cluster/build/query) — so that
the core scan can ``import auditor.graph.extract`` without dragging the heavy stack in."""

from auditor.graph.model import (
    EdgeKind,
    FileGraphFacts,
    GraphCluster,
    GraphEdge,
    GraphNode,
    NodeKind,
)

# Config override that forces fact extraction on for a `graph build` scan, whatever the repo set.
GRAPH_OVERRIDE: dict[str, dict[str, bool]] = {"graph": {"enabled": True}}

__all__ = [
    "GRAPH_OVERRIDE",
    "EdgeKind",
    "FileGraphFacts",
    "GraphCluster",
    "GraphEdge",
    "GraphNode",
    "NodeKind",
]
