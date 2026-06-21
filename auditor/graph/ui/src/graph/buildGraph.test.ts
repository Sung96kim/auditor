import { describe, it, expect } from "vitest";
import { buildGraphologyGraph, nodeColor, nodeSize } from "./buildGraph";
import type { GraphPayload } from "../types";

const P: GraphPayload = {
  meta: { theme: "dark", accent: "#7C7CFF", node_cap: 200 },
  clusters: [{ cluster_id: 0, label: "foo", member_count: 2, agg_rank: 0.5 }],
  nodes: [
    {
      id: "a",
      label: "a",
      type: "class",
      lang: "python",
      module: "m.py",
      path: "m.py",
      line: 1,
      rank: 0.4,
      cluster: 0,
      role: "production",
      findings: [],
    },
    {
      id: "b",
      label: "b",
      type: "method",
      lang: "python",
      module: "m.py",
      path: "m.py",
      line: 3,
      rank: 0.1,
      cluster: 0,
      role: "production",
      findings: [],
    },
  ],
  edges: [{ source: "a", target: "b", kind: "contains", weight: 1 }],
};

describe("buildGraphologyGraph", () => {
  it("overview renders one super-node per cluster", () => {
    const g = buildGraphologyGraph(P, { mode: "overview" });
    expect(g.order).toBe(1);
  });

  it("cluster view renders that cluster's members", () => {
    const g = buildGraphologyGraph(P, { mode: "cluster", clusterId: 0 });
    expect(g.order).toBe(2);
    expect(g.hasEdge("a", "b")).toBe(true);
  });

  it("ego view is the node + depth-1 neighbors", () => {
    const g = buildGraphologyGraph(P, { mode: "ego", nodeId: "a", depth: 1 });
    expect(g.hasNode("a")).toBe(true);
    expect(g.hasNode("b")).toBe(true);
  });

  it("color by type, size grows with rank", () => {
    expect(nodeColor("class")).toBe("#B57BFF");
    expect(nodeSize(0.4)).toBeGreaterThan(nodeSize(0.1));
  });

  it("does not set sigma-reserved 'type' attribute on nodes (uses 'kind')", () => {
    const cg = buildGraphologyGraph(P, { mode: "cluster", clusterId: 0 });
    cg.forEachNode((n) => {
      expect(cg.getNodeAttribute(n, "type")).toBeUndefined(); // sigma 'type' = program selector
    });
    // the symbol kind is preserved under 'kind'
    expect(cg.getNodeAttribute("a", "kind")).toBe("class");
    const eg = buildGraphologyGraph(P, { mode: "ego", nodeId: "a", depth: 1 });
    eg.forEachNode((n) => expect(eg.getNodeAttribute(n, "type")).toBeUndefined());
  });
});
