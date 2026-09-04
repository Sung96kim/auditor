/** The `/api/flow` walk as the page draws it: one flat layer list plus dagre's coordinates. */
import { graphlib, layout as dagreLayout } from "@dagrejs/dagre";
import type { FlowNode, HubMark, UnresolvedLeaf } from "../api/types";

export type { FlowNode, HubMark, UnresolvedLeaf };

export interface FlowRow {
  /** the node's position in the walk, because `flatten` emits a repeated id once per position. */
  key: string;
  id: string;
  depth: number;
  edge: string;
  /** spec 12.1: an unresolved leaf is highlighted, and `externally_bound` is dimmed instead. */
  unresolved: boolean;
  external: boolean;
  /** a hub is drawn collapsed with its fan, and only opens when the reader asks. */
  hub: number | null;
  collapsed: boolean;
  parent: string | null;
}

/** Walk the tree into layers, collapsing every hub unless its key is in `opened` (spec 12.1). */
export function flatten(root: FlowNode, opened: ReadonlySet<string> = new Set()): FlowRow[] {
  const out: FlowRow[] = [];
  const visit = (node: FlowNode, parent: string | null) => {
    const key = parent === null ? node.id : `${parent}>${node.id}`;
    const hub = node.hub ? node.hub.count : null;
    const collapsed = hub !== null && !opened.has(key);
    out.push({
      key,
      id: node.id,
      depth: node.depth,
      edge: node.edge ?? "",
      unresolved: node.unresolved.length > 0,
      // every leaf, not any: a node with one third-party call and one genuine gap is a gap
      external: node.unresolved.length > 0 && node.unresolved.every((u) => u.external),
      hub,
      collapsed,
      parent,
    });
    if (collapsed) return;
    for (const child of node.children) visit(child, key);
  };
  visit(root, null);
  return out;
}

export interface Placed extends FlowRow {
  x: number;
  y: number;
}

/** Where a row sits when dagre declines the graph, so a missing placement never throws in render. */
function fallback(view: FlowRow, index: number): Placed {
  return { ...view, x: view.depth * 200, y: index * 40 };
}

/** dagre's layered layout, left to right, which is the reading order of a call chain.
 *
 * dagre answers with node centres and the panel draws from the top left, so half a box comes
 * off each coordinate here rather than at one of the three call sites that read them.
 */
export function layered(views: FlowRow[], nodeWidth = 180, nodeHeight = 28): Placed[] {
  const g = new graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 12, ranksep: 60 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const view of views) g.setNode(view.key, { width: nodeWidth, height: nodeHeight });
  for (const view of views) if (view.parent) g.setEdge(view.parent, view.key);
  // the guard the shipped canvas already carries, in graphlib's own spelling: dagre ranks by
  // edge and throws on a graph with none, and a caught throw must still place every row
  if (!(g.nodeCount() > 1 && g.edgeCount() > 0)) return views.map(fallback);
  try {
    dagreLayout(g);
  } catch {
    return views.map(fallback);
  }
  return views.map((view, index) => {
    const placed = g.node(view.key) as { x: number; y: number } | undefined;
    if (!placed || !Number.isFinite(placed.x) || !Number.isFinite(placed.y)) {
      return fallback(view, index);
    }
    return { ...view, x: placed.x - nodeWidth / 2, y: placed.y - nodeHeight / 2 };
  });
}
