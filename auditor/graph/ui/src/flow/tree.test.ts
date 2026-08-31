import { describe, it, expect } from "vitest";
import { flatten, layered, type FlowNode } from "./tree";

function node(over: Partial<FlowNode> = {}): FlowNode {
  return {
    id: "m::a",
    kind: "function",
    edge: "calls",
    source: "static",
    depth: 0,
    seen_ref: false,
    cycle: false,
    stopped: false,
    hub: null,
    unresolved: [],
    children: [],
    ...over,
  };
}

const TREE = node({
  id: "root",
  children: [
    node({
      id: "hub",
      depth: 1,
      hub: { count: 61, kind: "fan_in", collapsed: true },
      children: [node({ id: "buried", depth: 2 })],
    }),
    node({
      id: "leaf",
      depth: 1,
      unresolved: [{ name: "requests.get", fact_kind: "call", reason: "third_party", external: true }],
    }),
    node({
      id: "gap",
      depth: 1,
      unresolved: [{ name: "helper", fact_kind: "call", reason: "unknown", external: false }],
    }),
  ],
});

describe("flattening a flow walk", () => {
  it("a hub is collapsed by default and its subtree is not drawn", () => {
    const ids = flatten(TREE).map((v) => v.id);
    expect(ids).toEqual(["root", "hub", "leaf", "gap"]);
    expect(flatten(TREE).find((v) => v.id === "hub")!.collapsed).toBe(true);
  });

  it("opening one hub draws its children and leaves other hubs closed", () => {
    const ids = flatten(TREE, new Set(["root>hub"])).map((v) => v.id);
    expect(ids).toContain("buried");
    expect(flatten(TREE, new Set(["root>hub"])).find((v) => v.id === "hub")!.collapsed).toBe(false);
  });

  it("the hub carries its fan so the collapsed node can say how much it hides", () => {
    expect(flatten(TREE).find((v) => v.id === "hub")!.hub).toBe(61);
  });

  it("an unresolved leaf is highlighted and an externally bound one is dimmed instead", () => {
    const views = flatten(TREE);
    const external = views.find((v) => v.id === "leaf")!;
    const gap = views.find((v) => v.id === "gap")!;
    expect(external.unresolved).toBe(true);
    expect(external.external).toBe(true);
    expect(gap.unresolved).toBe(true);
    expect(gap.external).toBe(false);
  });

  it("a resolved node is neither highlighted nor dimmed", () => {
    const root = flatten(TREE)[0];
    expect(root.unresolved).toBe(false);
    expect(root.external).toBe(false);
  });

  it("every node but the root names its parent, which is what dagre ranks on", () => {
    const views = flatten(TREE);
    expect(views[0].parent).toBeNull();
    expect(views.slice(1).every((v) => v.parent === "root")).toBe(true);
  });

  it("a node the walk emitted twice is two rows, so dagre cannot fold them onto one point", () => {
    const twice = node({
      id: "root",
      children: [
        node({ id: "shared", depth: 1 }),
        node({ id: "other", depth: 1, children: [node({ id: "shared", depth: 2, seen_ref: true })] }),
      ],
    });
    const rows = flatten(twice);
    expect(rows.filter((r) => r.id === "shared")).toHaveLength(2);
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
    const placed = layered(rows);
    expect(new Set(placed.map((p) => `${p.x},${p.y}`)).size).toBe(rows.length);
  });
});

describe("the dagre layered layout", () => {
  it("places deeper nodes further right, which is what layered means here", () => {
    const placed = layered(flatten(TREE));
    const root = placed.find((p) => p.id === "root")!;
    const leaf = placed.find((p) => p.id === "leaf")!;
    expect(leaf.x).toBeGreaterThan(root.x);
  });

  it("siblings share a rank and are separated vertically", () => {
    const placed = layered(flatten(TREE));
    const siblings = placed.filter((p) => p.parent === "root");
    expect(new Set(siblings.map((p) => p.x)).size).toBe(1);
    expect(new Set(siblings.map((p) => p.y)).size).toBe(siblings.length);
  });

  it("a single-node walk still lays out rather than throwing", () => {
    const placed = layered(flatten(node({ id: "only" })));
    expect(placed).toHaveLength(1);
    expect(Number.isFinite(placed[0].x)).toBe(true);
  });
});
