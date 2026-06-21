import Graph from "graphology";
import type { GraphPayload, NodeType } from "../types";
import { NODE_COLOR } from "../theme";

export type View =
  | { mode: "overview" }
  | { mode: "cluster"; clusterId: number }
  | { mode: "ego"; nodeId: string; depth: number };

export function nodeColor(type: NodeType | string): string {
  return NODE_COLOR[type] ?? "#888888";
}

export function nodeSize(rank: number): number {
  return 4 + Math.sqrt(Math.max(rank, 0)) * 40;
}

export function buildGraphologyGraph(payload: GraphPayload, view: View): Graph {
  const g = new Graph({ multi: false, type: "undirected" });

  if (view.mode === "overview") {
    // One node per cluster, sorted by cluster_id for determinism
    const sortedClusters = [...payload.clusters].sort(
      (a, b) => a.cluster_id - b.cluster_id
    );
    for (const cluster of sortedClusters) {
      g.addNode(`cluster:${cluster.cluster_id}`, {
        label: cluster.label,
        size: nodeSize(cluster.agg_rank),
        color: NODE_COLOR["module"] ?? "#F0A848",
        clusterId: cluster.cluster_id,
      });
    }
    // Count cross-cluster edges
    const edgeCounts = new Map<string, number>();
    for (const edge of payload.edges) {
      const srcNode = payload.nodes.find((n) => n.id === edge.source);
      const tgtNode = payload.nodes.find((n) => n.id === edge.target);
      if (!srcNode || !tgtNode) continue;
      const sc = srcNode.cluster;
      const tc = tgtNode.cluster;
      if (sc === null || tc === null || sc === tc) continue;
      const key =
        sc < tc ? `cluster:${sc}---cluster:${tc}` : `cluster:${tc}---cluster:${sc}`;
      edgeCounts.set(key, (edgeCounts.get(key) ?? 0) + 1);
    }
    for (const [key, count] of edgeCounts) {
      const [src, tgt] = key.split("---");
      if (g.hasNode(src) && g.hasNode(tgt) && !g.hasEdge(src, tgt)) {
        g.addEdge(src, tgt, { weight: count });
      }
    }
  } else if (view.mode === "cluster") {
    const { clusterId } = view;
    const members = payload.nodes
      .filter((n) => n.cluster === clusterId)
      .sort((a, b) => a.id.localeCompare(b.id));
    for (const node of members) {
      g.addNode(node.id, {
        label: node.label,
        size: nodeSize(node.rank),
        color: nodeColor(node.type),
        type: node.type,
        rank: node.rank,
      });
    }
    const memberIds = new Set(members.map((n) => n.id));
    for (const edge of payload.edges) {
      if (memberIds.has(edge.source) && memberIds.has(edge.target)) {
        if (!g.hasEdge(edge.source, edge.target)) {
          g.addEdge(edge.source, edge.target, {
            kind: edge.kind,
            weight: edge.weight,
          });
        }
      }
    }
  } else if (view.mode === "ego") {
    const { nodeId, depth } = view;
    // BFS over edges (bidirectional)
    const visited = new Set<string>();
    const queue: Array<{ id: string; d: number }> = [{ id: nodeId, d: 0 }];
    visited.add(nodeId);
    while (queue.length > 0) {
      const item = queue.shift()!;
      if (item.d >= depth) continue;
      for (const edge of payload.edges) {
        let neighbor: string | null = null;
        if (edge.source === item.id && !visited.has(edge.target)) {
          neighbor = edge.target;
        } else if (edge.target === item.id && !visited.has(edge.source)) {
          neighbor = edge.source;
        }
        if (neighbor !== null) {
          visited.add(neighbor);
          queue.push({ id: neighbor, d: item.d + 1 });
        }
      }
    }
    // Insert nodes in sorted id order for determinism
    const sortedVisited = [...visited].sort();
    const nodeMap = new Map(payload.nodes.map((n) => [n.id, n]));
    for (const id of sortedVisited) {
      const node = nodeMap.get(id);
      if (!node) continue;
      g.addNode(id, {
        label: node.label,
        size: nodeSize(node.rank),
        color: nodeColor(node.type),
        type: node.type,
        rank: node.rank,
      });
    }
    const egoIds = visited;
    for (const edge of payload.edges) {
      if (egoIds.has(edge.source) && egoIds.has(edge.target)) {
        if (!g.hasEdge(edge.source, edge.target)) {
          g.addEdge(edge.source, edge.target, {
            kind: edge.kind,
            weight: edge.weight,
          });
        }
      }
    }
  }

  return g;
}
