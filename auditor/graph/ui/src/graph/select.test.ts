import { describe, it, expect } from "vitest";
import { neighborIds } from "./select";
import type { GraphPayload } from "../types";

const P: GraphPayload = {
  meta: { theme: "dark", accent: "#7C7CFF", node_cap: 200 },
  clusters: [{ cluster_id: 0, label: "foo", member_count: 3, agg_rank: 0.5 }],
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
    {
      id: "c",
      label: "c",
      type: "function",
      lang: "python",
      module: "m.py",
      path: "m.py",
      line: 5,
      rank: 0.2,
      cluster: 0,
      role: "production",
      findings: [],
    },
  ],
  edges: [
    { source: "a", target: "b", kind: "calls", weight: 1 },
    { source: "c", target: "a", kind: "calls", weight: 1 },
  ],
};

describe("neighborIds", () => {
  it("returns direct neighbors in both directions", () => {
    const result = neighborIds(P, "a");
    expect(result).toEqual(new Set(["b", "c"]));
  });

  it("returns outgoing neighbors only when source", () => {
    const result = neighborIds(P, "b");
    expect(result).toEqual(new Set(["a"]));
  });

  it("returns incoming neighbor when only a target", () => {
    const result = neighborIds(P, "c");
    expect(result).toEqual(new Set(["a"]));
  });

  it("returns empty set for unknown node id", () => {
    const result = neighborIds(P, "unknown-xyz");
    expect(result).toEqual(new Set());
  });

  it("does not include the node itself", () => {
    const result = neighborIds(P, "a");
    expect(result.has("a")).toBe(false);
  });
});
