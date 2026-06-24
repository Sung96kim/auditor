import { describe, it, expect } from "vitest";
import { toJson, toDot, toOutline, type TextNode, type TextEdge } from "./textViews";

// module m.py contains validate() and Helper; validate() calls Helper and references Err.
const NODES: TextNode[] = [
  { id: "m.py", label: "m.py", type: "module", rank: 0.9, cluster: 1 },
  { id: "m.py::validate", label: "validate", type: "function", line: 10, module: "m", rank: 0.5, cluster: 1 },
  { id: "m.py::Helper", label: "Helper", type: "class", line: 30, module: "m", rank: 0.3, cluster: 1 },
  { id: "e.py::Err", label: "Err", type: "class", line: 1, module: "e", rank: 0.1, cluster: 2 },
];
const EDGES: TextEdge[] = [
  { source: "m.py", target: "m.py::validate", kind: "contains" },
  { source: "m.py", target: "m.py::Helper", kind: "contains" },
  { source: "m.py::validate", target: "m.py::Helper", kind: "calls" },
  { source: "m.py::validate", target: "e.py::Err", kind: "references_type" },
];

describe("toJson", () => {
  it("emits sorted nodes + edges with kept fields", () => {
    const obj = JSON.parse(toJson(NODES, EDGES));
    expect(obj.nodes.map((n: { id: string }) => n.id)).toEqual([
      "e.py::Err",
      "m.py",
      "m.py::Helper",
      "m.py::validate",
    ]);
    const v = obj.nodes.find((n: { id: string }) => n.id === "m.py::validate");
    expect(v).toMatchObject({ label: "validate", type: "function", line: 10, module: "m" });
    expect(obj.edges[0]).toEqual({
      source: "m.py",
      target: "m.py::Helper",
      kind: "contains",
    });
  });

  it("is deterministic regardless of input order", () => {
    const reversed = toJson([...NODES].reverse(), [...EDGES].reverse());
    expect(reversed).toBe(toJson(NODES, EDGES));
  });
});

describe("toDot", () => {
  it("emits a digraph with labelled edges", () => {
    const dot = toDot(NODES, EDGES);
    expect(dot.startsWith("digraph codebase {")).toBe(true);
    expect(dot).toContain("rankdir=LR;");
    expect(dot).toContain('"m.py::validate" -> "m.py::Helper" [label="calls"];');
    expect(dot).toContain('"m.py::validate" -> "e.py::Err" [label="references_type"];');
    expect(dot.trimEnd().endsWith("}")).toBe(true);
  });
});

describe("toOutline", () => {
  it("nests contained members under their parent and annotates relationships", () => {
    const out = toOutline(NODES, EDGES);
    const lines = out.split("\n");
    // module is a root at depth 0; Err is an external reference with no in-scope parent,
    // so it's also a root (roots are sorted by name).
    expect(lines).toContain("m.py [module]");
    expect(lines).toContain("Err [class]");
    // m.py's members are indented beneath it
    expect(out).toContain("  Helper [class]");
    expect(out).toContain("  validate [function]");
    // validate's outgoing relationships are listed under it, grouped by kind
    expect(out).toContain("    → calls: Helper");
    expect(out).toContain("    → references_type: Err");
    // Err is referenced into, so it carries an incoming annotation
    expect(out).toContain("← references_type: validate");
  });

  it("shows member count for cluster nodes", () => {
    const out = toOutline(
      [{ id: "cluster:1", label: "auth", type: "cluster", rank: 1, count: 12 }],
      []
    );
    expect(out).toBe("auth [cluster, 12]");
  });
});
