import { describe, it, expect } from "vitest";
import { EDGE_PROGRAMS } from "../components/GraphCanvas";
import {
  EDGE_TYPES,
  edgeKey,
  refinedEdgeKeys,
  refinedEdgeType,
  refinedNodeIds,
  unconfirmedEdgeKeys,
} from "./refined";
import type { GraphPayload } from "../types";

const P: GraphPayload = {
  meta: { theme: "dark", accent: "#7C7CFF", node_cap: 200 },
  clusters: [],
  nodes: [
    { id: "a", label: "a", type: "class", lang: "python", module: "m.py", path: "m.py",
      line: 1, rank: 0.4, cluster: null, role: "production", findings: [] },
    { id: "b", label: "b", type: "method", lang: "python", module: "m.py", path: "m.py",
      line: 3, rank: 0.1, cluster: null, role: "production", findings: [],
      refined: true, annotation: "renamed" },
  ],
  edges: [
    { source: "a", target: "b", kind: "calls", weight: 1, provenance: "deterministic", confirmed: false },
    { source: "b", target: "a", kind: "calls", weight: 1, provenance: "refined", confirmed: true },
    { source: "a", target: "a", kind: "calls", weight: 1, provenance: "refined", confirmed: false },
  ],
};

describe("the refinement overlay", () => {
  it("highlights only the nodes a refinement touched", () => {
    expect([...refinedNodeIds(P)]).toEqual(["b"]);
  });

  it("a deterministic edge is never part of the overlay", () => {
    expect(refinedEdgeKeys(P).has(edgeKey("a", "b", "calls"))).toBe(false);
    expect(refinedEdgeKeys(P).has(edgeKey("b", "a", "calls"))).toBe(true);
  });

  it("an unconfirmed overlay edge is a subset of the overlay, drawn provisionally", () => {
    expect([...unconfirmedEdgeKeys(P)]).toEqual([edgeKey("a", "a", "calls")]);
  });

  it("a payload from graph serve, whose edges carry no provenance, has no overlay", () => {
    const bare: GraphPayload = {
      ...P,
      nodes: [P.nodes[0]],
      edges: [{ source: "a", target: "a", kind: "calls", weight: 1 }],
    };
    expect(refinedNodeIds(bare).size).toBe(0);
    expect(refinedEdgeKeys(bare).size).toBe(0);
  });

  it("the key separates the triple, so two ids carrying a space cannot collide", () => {
    expect(edgeKey("a b", "c", "calls")).not.toBe(edgeKey("a", "b c", "calls"));
  });
});

describe("the overlay's edge programs", () => {
  it("every type the reducer can return has a program registered for it", () => {
    // sigma looks `type` up in `edgeProgramClasses` and throws inside its render loop on a miss,
    // which takes the canvas down; the map it is given replaces sigma's defaults wholesale
    for (const confirmed of [true, false]) {
      expect(Object.keys(EDGE_PROGRAMS)).toContain(refinedEdgeType(confirmed));
    }
    expect(Object.keys(EDGE_PROGRAMS).sort()).toEqual(Object.values(EDGE_TYPES).sort());
  });

  it("a confirmed and an unconfirmed overlay edge are drawn by different programs", () => {
    expect(refinedEdgeType(true)).not.toBe(refinedEdgeType(false));
  });
});
