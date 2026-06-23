/** Text renderings of a graph subgraph — the data behind the TEXT view mode.
 *
 * These are pure functions over plain {@link TextNode}/{@link TextEdge} arrays (no graphology
 * dependency) so they're trivially testable. The component converts the current view's
 * graphology subgraph into these arrays, then picks a format. All output is deterministic
 * (sorted) so the same view always renders identically. */

export interface TextNode {
  id: string;
  label: string;
  type: string; // function | method | class | module | cluster
  line?: number;
  module?: string;
  rank: number;
  cluster?: number | null;
  count?: number; // member count, for cluster nodes in the overview
}

export interface TextEdge {
  source: string;
  target: string;
  kind: string;
}

const byId = (a: { id: string }, b: { id: string }): number => a.id.localeCompare(b.id);

const byEdge = (a: TextEdge, b: TextEdge): number =>
  a.source.localeCompare(b.source) ||
  a.target.localeCompare(b.target) ||
  a.kind.localeCompare(b.kind);

/** DOT-quote: wrap in double quotes, escaping internal quotes/backslashes. */
const q = (s: string): string => JSON.stringify(s);

const push = <K, V>(map: Map<K, V[]>, key: K, value: V): void => {
  const arr = map.get(key);
  if (arr) arr.push(value);
  else map.set(key, [value]);
};

/** Sorted, deterministic JSON of the scoped subgraph. */
export function toJson(nodes: TextNode[], edges: TextEdge[]): string {
  const outNodes = [...nodes].sort(byId).map((n) => ({
    id: n.id,
    label: n.label,
    type: n.type,
    ...(n.line != null ? { line: n.line } : {}),
    ...(n.module ? { module: n.module } : {}),
    rank: Math.round(n.rank * 1e6) / 1e6,
    ...(n.cluster != null ? { cluster: n.cluster } : {}),
    ...(n.count != null ? { members: n.count } : {}),
  }));
  const outEdges = [...edges]
    .sort(byEdge)
    .map((e) => ({ source: e.source, target: e.target, kind: e.kind }));
  return JSON.stringify({ nodes: outNodes, edges: outEdges }, null, 2);
}

/** Graphviz DOT for the scoped subgraph — paste into any Graphviz tool. */
export function toDot(nodes: TextNode[], edges: TextEdge[]): string {
  const lines = ["digraph codebase {", "  rankdir=LR;", "  node [shape=box, style=rounded];"];
  for (const n of [...nodes].sort(byId)) {
    lines.push(`  ${q(n.id)} [label=${q(n.label)}];`);
  }
  for (const e of [...edges].sort(byEdge)) {
    const label = e.kind ? ` [label=${q(e.kind)}]` : "";
    lines.push(`  ${q(e.source)} -> ${q(e.target)}${label};`);
  }
  lines.push("}");
  return lines.join("\n");
}

/** Indented contains-tree (module → class → method); each entry annotated with its typed
 * non-contains edges (→ outgoing, ← incoming). */
export function toOutline(nodes: TextNode[], edges: TextEdge[]): string {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const name = (id: string): string =>
    nodeById.get(id)?.label ?? id.split("::").pop() ?? id;

  const children = new Map<string, string[]>();
  const containsTarget = new Set<string>();
  const outRels = new Map<string, TextEdge[]>();
  const inRels = new Map<string, TextEdge[]>();
  for (const e of edges) {
    if (e.kind === "contains") {
      push(children, e.source, e.target);
      containsTarget.add(e.target);
    } else {
      push(outRels, e.source, e);
      push(inRels, e.target, e);
    }
  }

  /** Group a node's relationship edges by kind → sorted unique neighbour names. */
  const grouped = (es: TextEdge[], field: "source" | "target"): Map<string, string[]> => {
    const sets = new Map<string, Set<string>>();
    for (const e of es) {
      const set = sets.get(e.kind) ?? new Set<string>();
      set.add(name(e[field]));
      sets.set(e.kind, set);
    }
    const out = new Map<string, string[]>();
    for (const k of [...sets.keys()].sort()) out.set(k, [...sets.get(k)!].sort());
    return out;
  };

  const sortIds = (ids: string[]): string[] =>
    [...ids].sort((a, b) => name(a).localeCompare(name(b)) || a.localeCompare(b));

  const lines: string[] = [];
  const visited = new Set<string>();
  const emit = (id: string, depth: number): void => {
    if (visited.has(id)) return;
    visited.add(id);
    const node = nodeById.get(id);
    const pad = "  ".repeat(depth);
    const meta = node?.count != null ? ` [${node.type}, ${node.count}]` : ` [${node?.type ?? "?"}]`;
    lines.push(`${pad}${node?.label ?? name(id)}${meta}`);
    for (const [kind, names] of grouped(outRels.get(id) ?? [], "target")) {
      lines.push(`${pad}  → ${kind}: ${names.join(", ")}`);
    }
    for (const [kind, names] of grouped(inRels.get(id) ?? [], "source")) {
      lines.push(`${pad}  ← ${kind}: ${names.join(", ")}`);
    }
    for (const child of sortIds(children.get(id) ?? [])) emit(child, depth + 1);
  };

  // Roots = nodes that are never a contains-target (modules / orphans), sorted.
  for (const id of sortIds(nodes.map((n) => n.id).filter((id) => !containsTarget.has(id)))) {
    emit(id, 0);
  }
  // Anything unreached (contains-cycle or member without an in-scope parent) is emitted flat.
  for (const n of nodes) if (!visited.has(n.id)) emit(n.id, 0);

  return lines.join("\n");
}
