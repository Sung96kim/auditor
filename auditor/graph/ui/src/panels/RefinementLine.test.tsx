import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RefinementLine from "./RefinementLine";
import { refinementPayload, refinementRow } from "../api/wire.fixture";
import type { RefinementRow } from "../api/types";

/** One row per `RefinementKind`, filled in the way `RefinementRowPayload.of` flattens it.
 *
 * `resolve_ambiguous` carries its node in `src` and its chosen candidate in `dst`, because
 * `edge_pair()` reads that pair off the payload; the rest follow `_REQUIRED_BY_KIND`.
 */
const KINDS: [string, Partial<RefinementRow>, string][] = [
  [
    "add_edge",
    { src: "pkg/dispatch.py::perform", dst: "pkg/core.py::Engine.start" },
    "pkg/dispatch.py::perform to pkg/core.py::Engine.start",
  ],
  [
    "retarget_edge",
    {
      src: "pkg/dispatch.py::relay",
      from_dst: "pkg/util.py::fmt",
      dst: "pkg/util.py::slugify",
    },
    "pkg/dispatch.py::relay: pkg/util.py::fmt to pkg/util.py::slugify",
  ],
  [
    "confirm_edge",
    { src: "pkg/dispatch.py::relay", dst: "pkg/util.py::slugify" },
    "pkg/dispatch.py::relay to pkg/util.py::slugify",
  ],
  [
    "resolve_ambiguous",
    {
      src: "pkg/cli.py::run",
      dst: "pkg/util.py::fmt",
      node_id: "pkg/cli.py::run",
    },
    "pkg/cli.py::run to pkg/util.py::fmt",
  ],
  [
    "relabel_cluster",
    {
      src: null,
      dst: null,
      members: ["pkg/core.py::Engine", "pkg/core.py::boot"],
      payload: refinementPayload({ label: "engine startup" }),
    },
    "engine startup: pkg/core.py::Engine, pkg/core.py::boot",
  ],
  [
    "move_node",
    {
      src: null,
      dst: null,
      node_id: "pkg/util.py::fmt",
      members: ["pkg/core.py::Engine"],
    },
    "pkg/util.py::fmt to pkg/core.py::Engine",
  ],
  [
    "annotate_node",
    { src: null, dst: null, node_id: "pkg/core.py::boot" },
    "pkg/core.py::boot",
  ],
  [
    "unresolvable",
    { src: null, dst: null, node_id: "pkg/cli.py::audit" },
    "pkg/cli.py::audit",
  ],
];

afterEach(cleanup);

describe("what a refinement row says it is about, for every kind the wire serves", () => {
  it.each(KINDS)("a %s names its kind and its target", (kind, over, target) => {
    render(<RefinementLine row={refinementRow({ kind, ...over })} />);
    const line = screen.getByText(new RegExp(kind));
    expect(line.textContent).toBe(`[A] ${kind} ${target}`);
  });

  it.each(KINDS)("a %s never draws a dash where a value belongs", (kind, over) => {
    render(<RefinementLine row={refinementRow({ kind, ...over })} />);
    const line = screen.getByText(new RegExp(kind));
    expect(line.textContent).not.toMatch(/(^| )-( |$)/);
  });

  it("a row whose kind recorded no target at all says that, rather than showing nothing", () => {
    render(
      <RefinementLine
        row={refinementRow({ kind: "annotate_node", src: null, dst: null, node_id: null })}
      />,
    );
    expect(screen.getByText(/annotate_node/).textContent).toBe(
      "[A] annotate_node no target recorded",
    );
  });

  it("the trailing mark is drawn beside the row, not in place of its target", () => {
    render(
      <RefinementLine
        row={refinementRow({ kind: "add_edge" })}
        trailing={<span> drifted</span>}
      />,
    );
    expect(screen.getByText(/add_edge/).textContent).toBe(
      "[A] add_edge app/cli.py::main to app/engine.py::run drifted",
    );
  });
});
