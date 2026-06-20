"""Semantic codebase graph. NOTE: this package __init__ stays stdlib-only — it must never import
the numpy/scikit-learn modules (naming/usage/rank/cluster/build/query), so that the core scan can
``import auditor.graph.extract`` without the optional ``[graph]`` extra installed."""

from auditor.graph.model import (
    EdgeKind,
    FileGraphFacts,
    GraphCluster,
    GraphEdge,
    GraphNode,
    NodeKind,
)

__all__ = [
    "EdgeKind",
    "FileGraphFacts",
    "GraphCluster",
    "GraphEdge",
    "GraphNode",
    "NodeKind",
]
