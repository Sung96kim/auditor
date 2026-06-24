import type { GNode, GEdge, GraphPayload, NodeType } from "../types";

export interface FilterOptions {
  langs: Set<string>;
  types: Set<NodeType | string>;
  query: string;
}

export interface FilterResult {
  nodes: GNode[];
  edges: GEdge[];
}

/** Pure, deterministic filter over a payload's nodes and edges. */
export function applyFilters(
  payload: GraphPayload,
  { langs, types, query }: FilterOptions
): FilterResult {
  const q = query.trim().toLowerCase();

  const nodes = payload.nodes.filter((n) => {
    if (!langs.has(n.lang)) return false;
    if (!types.has(n.type)) return false;
    if (q !== "" && !n.label.toLowerCase().includes(q) && !n.id.toLowerCase().includes(q)) {
      return false;
    }
    return true;
  });

  const visibleIds = new Set(nodes.map((n) => n.id));

  const edges = payload.edges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target)
  );

  return { nodes, edges };
}
