import { describe, it, expect } from "vitest";
import { applyFilters } from "./filter";
import type { GraphPayload } from "../types";

const P: GraphPayload = {
  meta: { theme: "dark", accent: "#7C7CFF", node_cap: 200 },
  clusters: [],
  nodes: [
    {
      id: "a.py::MyClass",
      label: "MyClass",
      type: "class",
      lang: "python",
      module: "a",
      path: "a.py",
      line: 1,
      rank: 0.5,
      cluster: 0,
      role: "hub",
      findings: ["FIND-001"],
    },
    {
      id: "b.ts::myFn",
      label: "myFn",
      type: "function",
      lang: "typescript",
      module: "b",
      path: "b.ts",
      line: 5,
      rank: 0.3,
      cluster: 0,
      role: "worker",
      findings: [],
    },
    {
      id: "c.py::run",
      label: "run",
      type: "method",
      lang: "python",
      module: "c",
      path: "c.py",
      line: 10,
      rank: 0.2,
      cluster: 1,
      role: "utility",
      findings: [],
    },
  ],
  edges: [
    { source: "a.py::MyClass", target: "b.ts::myFn", kind: "calls", weight: 1 },
    { source: "b.ts::myFn", target: "c.py::run", kind: "calls", weight: 0.5 },
    { source: "a.py::MyClass", target: "c.py::run", kind: "calls", weight: 0.8 },
  ],
};

describe("applyFilters", () => {
  it("returns all nodes and edges when all langs and types are included", () => {
    const result = applyFilters(P, {
      langs: new Set(["python", "typescript"]),
      types: new Set(["class", "function", "method", "module"]),
      query: "",
    });
    expect(result.nodes).toHaveLength(3);
    expect(result.edges).toHaveLength(3);
  });

  it("language filter hides nodes whose lang is not in the set", () => {
    const result = applyFilters(P, {
      langs: new Set(["python"]),
      types: new Set(["class", "function", "method", "module"]),
      query: "",
    });
    expect(result.nodes.map((n) => n.id)).not.toContain("b.ts::myFn");
    expect(result.nodes).toHaveLength(2);
  });

  it("type filter hides nodes whose type is not in the set", () => {
    const result = applyFilters(P, {
      langs: new Set(["python", "typescript"]),
      types: new Set(["class"]),
      query: "",
    });
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].id).toBe("a.py::MyClass");
  });

  it("query keeps only nodes whose label or id contains the search term (case-insensitive)", () => {
    const result = applyFilters(P, {
      langs: new Set(["python", "typescript"]),
      types: new Set(["class", "function", "method", "module"]),
      query: "my",
    });
    // "MyClass" matches "my" (case-insensitive), "myFn" matches "my"
    expect(result.nodes.map((n) => n.id)).toContain("a.py::MyClass");
    expect(result.nodes.map((n) => n.id)).toContain("b.ts::myFn");
    expect(result.nodes.map((n) => n.id)).not.toContain("c.py::run");
  });

  it("query is case-insensitive", () => {
    const result = applyFilters(P, {
      langs: new Set(["python", "typescript"]),
      types: new Set(["class", "function", "method", "module"]),
      query: "MYCLASS",
    });
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].id).toBe("a.py::MyClass");
  });

  it("edges with a hidden endpoint are dropped", () => {
    // Only python → b.ts::myFn is hidden → edges involving it are dropped
    const result = applyFilters(P, {
      langs: new Set(["python"]),
      types: new Set(["class", "function", "method", "module"]),
      query: "",
    });
    // Only the a→c edge should remain (both python nodes)
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].source).toBe("a.py::MyClass");
    expect(result.edges[0].target).toBe("c.py::run");
  });

  it("edges are dropped when a query hides a node", () => {
    const result = applyFilters(P, {
      langs: new Set(["python", "typescript"]),
      types: new Set(["class", "function", "method", "module"]),
      query: "run",
    });
    // Only c.py::run visible
    expect(result.nodes).toHaveLength(1);
    expect(result.edges).toHaveLength(0);
  });

  it("empty langs set hides all nodes", () => {
    const result = applyFilters(P, {
      langs: new Set<string>(),
      types: new Set(["class", "function", "method", "module"]),
      query: "",
    });
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });
});
