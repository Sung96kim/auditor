import { describe, it, expect } from "vitest";
import { flatten, layered } from "./tree";
import { flowNode as node, hubMark, unresolvedLeaf } from "../api/wire.fixture";

/** What `/api/flow?expand_hubs=1` answers with: a hub that carries its fan and its children.
 *
 * With `expand_hubs` off the walk `continue`s before the children, so a collapsed hub arrives
 * with none and the client has nothing to open. The two shapes are pinned apart below.
 */
const TREE = node({
  id: "root",
  children: [
    node({
      id: "hub",
      depth: 1,
      hub: hubMark({ count: 61, kind: "fan_in", collapsed: false }),
      children: [node({ id: "buried", depth: 2 })],
    }),
    node({
      id: "leaf",
      depth: 1,
      unresolved: [unresolvedLeaf({ reason: "third_party" })],
    }),
    node({
      id: "gap",
      depth: 1,
      unresolved: [unresolvedLeaf({ name: "helper", reason: "unknown", external: false })],
    }),
  ],
});

/** What the same walk answers with the flag off: the hub is marked collapsed and holds nothing. */
const UNEXPANDED = node({
  id: "root",
  children: [
    node({ id: "hub", depth: 1, hub: hubMark({ count: 61, kind: "fan_in" }), children: [] }),
  ],
});

describe("flattening a flow walk", () => {
  it("a hub is collapsed by default and its subtree is not drawn", () => {
    const ids = flatten(TREE).map((v) => v.id);
    expect(ids).toEqual(["root", "hub", "leaf", "gap"]);
    expect(flatten(TREE).find((v) => v.id === "hub")!.collapsed).toBe(true);
  });

  it("opening one hub draws the children the expanded walk sent with it", () => {
    const ids = flatten(TREE, new Set(["root>hub"])).map((v) => v.id);
    expect(ids).toContain("buried");
    expect(flatten(TREE, new Set(["root>hub"])).find((v) => v.id === "hub")!.collapsed).toBe(false);
  });

  it("a hub the walk stopped at has nothing to draw, opened or not", () => {
    // the shape `/api/flow` serves without `expand_hubs=1`: the control needs the refetch
    const shut = flatten(UNEXPANDED).find((v) => v.id === "hub")!;
    expect(shut.collapsed).toBe(true);
    expect(flatten(UNEXPANDED, new Set(["root>hub"])).map((v) => v.id)).toEqual([
      "root",
      "hub",
    ]);
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

  it("one third-party call beside one genuine gap is a gap, because external means every leaf", () => {
    const mixed = node({
      id: "mixed",
      unresolved: [
        unresolvedLeaf({ external: true }),
        unresolvedLeaf({ name: "helper", external: false }),
      ],
    });
    const row = flatten(mixed)[0];
    expect(row.unresolved).toBe(true);
    expect(row.external).toBe(false);
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

  it("the coordinates are the node's own corner, which is where the panel draws from", () => {
    // dagre answers with centres; both branches of `layered` have to agree about which corner
    const placed = layered(flatten(TREE), 180, 28);
    const fallen = layered(flatten(node({ id: "only" })), 180, 28);
    expect(Math.min(...placed.map((p) => p.x))).toBe(0);
    expect(Math.min(...placed.map((p) => p.y))).toBe(0);
    expect(fallen[0].x).toBe(0);
  });

  it("a single-node walk still lays out rather than throwing", () => {
    const placed = layered(flatten(node({ id: "only" })));
    expect(placed).toHaveLength(1);
    expect(Number.isFinite(placed[0].x)).toBe(true);
  });
});
