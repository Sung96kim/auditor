import { describe, expect, it } from "vitest";
import { breadcrumbPath } from "./breadcrumb";
import type { GraphPayload } from "../types";

const payload: GraphPayload = {
  meta: { theme: "dark", accent: "#7C7CFF", node_cap: 200 },
  clusters: [
    { cluster_id: 0, label: "core", member_count: 2, agg_rank: 0.9 },
    { cluster_id: 1, label: "utils", member_count: 1, agg_rank: 0.4 },
  ],
  nodes: [
    {
      id: "a::build",
      label: "build",
      type: "function",
      lang: "python",
      module: "a",
      path: "a.py",
      line: 1,
      rank: 0.4,
      cluster: 0,
      role: "hub",
      findings: [],
    },
    {
      id: "b::run",
      label: "run",
      type: "function",
      lang: "python",
      module: "b",
      path: "b.py",
      line: 2,
      rank: 0.2,
      cluster: 1,
      role: "worker",
      findings: [],
    },
  ],
  edges: [],
};

describe("breadcrumbPath", () => {
  it("overview → [Overview]", () => {
    const crumbs = breadcrumbPath({ mode: "overview" }, payload, null);
    expect(crumbs).toEqual([{ label: "Overview", target: { kind: "overview" } }]);
  });

  it("cluster view → [Overview, Cluster \"<label>\"]", () => {
    const crumbs = breadcrumbPath({ mode: "cluster", clusterId: 0 }, payload, null);
    expect(crumbs.map((c) => c.label)).toEqual(["Overview", 'Cluster "core"']);
    expect(crumbs[1].target).toEqual({ kind: "cluster", clusterId: 0 });
  });

  it("falls back to cluster id when label missing", () => {
    const crumbs = breadcrumbPath({ mode: "cluster", clusterId: 99 }, payload, null);
    expect(crumbs.map((c) => c.label)).toEqual(["Overview", 'Cluster "99"']);
  });

  it("selected node in cluster view → [Overview, Cluster, <node label>]", () => {
    const crumbs = breadcrumbPath(
      { mode: "cluster", clusterId: 0 },
      payload,
      "a::build"
    );
    expect(crumbs.map((c) => c.label)).toEqual([
      "Overview",
      'Cluster "core"',
      "build",
    ]);
  });

  it("selected node from overview → [Overview, Cluster, <node label>]", () => {
    const crumbs = breadcrumbPath({ mode: "overview" }, payload, "b::run");
    expect(crumbs.map((c) => c.label)).toEqual([
      "Overview",
      'Cluster "utils"',
      "run",
    ]);
    expect(crumbs[2].target).toEqual({ kind: "cluster", clusterId: 1 });
  });

  it("ego view derives cluster crumb from the ego node", () => {
    const crumbs = breadcrumbPath(
      { mode: "ego", nodeId: "a::build", depth: 1 },
      payload,
      "a::build"
    );
    expect(crumbs.map((c) => c.label)).toEqual([
      "Overview",
      'Cluster "core"',
      "build",
    ]);
  });
});
