import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import RefinementList from "./RefinementList";
import { refinementRow } from "../api/wire.fixture";
import type { RefinementRow } from "../api/types";

const refinement = refinementRow;

function body(rows: RefinementRow[], refinement_count: number, truncated: boolean) {
  return new Response(JSON.stringify({ refinements: { rows, refinement_count, truncated } }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function serve(rows: RefinementRow[], refinement_count: number, truncated: boolean) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(body(rows, refinement_count, truncated))));
  return render(<RefinementList base="/" repo="/w" />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the refinement list", () => {
  it("a status with rows gets a heading and a count", async () => {
    serve([refinement()], 1, false);
    expect((await screen.findByText(/active \(1\)/)).textContent).toBe("active (1)");
  });

  it("the five statuses with nothing in them report a zero without each taking a heading", async () => {
    serve([refinement()], 1, false);
    await screen.findByText(/active \(1\)/);
    for (const status of ["pending", "pinned", "redundant", "rejected", "reverted"]) {
      expect(screen.getByText(`${status} 0`)).not.toBeNull();
      expect(screen.queryByText(`${status} (0)`)).toBeNull();
    }
  });

  it("a truncated list says how many rows it is not showing", async () => {
    serve([refinement()], 9, true);
    const strip = screen.getByTestId("RefinementList").firstElementChild;
    await screen.findByText(/active \(1\)/);
    expect(strip?.textContent).toBe("Refinements1 of 9");
  });

  it("an untruncated list shows the count alone, with nothing to qualify", async () => {
    serve([refinement()], 1, false);
    await screen.findByText(/active \(1\)/);
    expect(screen.getByTestId("RefinementList").firstElementChild?.textContent).toBe(
      "Refinements1",
    );
  });

  it("Retry issues a second request, so the error does not become a permanent spinner", async () => {
    let call = 0;
    const fetcher = vi.fn(() =>
      call++ === 0
        ? Promise.reject(new Error("connection refused"))
        : Promise.resolve(body([refinement()], 1, false)),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<RefinementList base="/" repo="/w" />);
    const retry = await screen.findByRole("button", { name: "Retry" });
    fireEvent.click(retry);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/active \(1\)/)).not.toBeNull();
    expect(screen.queryByText(/Loading refinements/)).toBeNull();
  });

  it("a cluster relabel names its new label and its members, never a bare dash", async () => {
    serve(
      [
        refinement({
          kind: "relabel_cluster",
          tier: "B",
          status: "pinned",
          src: null,
          dst: null,
          members: ["pkg/core.py::Engine", "pkg/core.py::boot"],
          payload: { label: "engine startup" },
        }),
      ],
      1,
      false,
    );
    expect((await screen.findByText(/relabel_cluster/)).textContent).toBe(
      "[B] relabel_cluster engine startup: pkg/core.py::Engine, pkg/core.py::boot",
    );
    expect(screen.queryByText(/relabel_cluster -$/)).toBeNull();
  });

  it("a move names the node it moved and where it moved to", async () => {
    serve(
      [
        refinement({
          kind: "move_node",
          src: null,
          dst: null,
          node_id: "pkg/util.py::fmt",
          members: ["pkg/core.py::Engine"],
        }),
      ],
      1,
      false,
    );
    expect((await screen.findByText(/move_node/)).textContent).toBe(
      "[A] move_node pkg/util.py::fmt to pkg/core.py::Engine",
    );
  });

  it("a drifted row is marked in its own tone, not in a parenthesis in the same grey", async () => {
    serve([refinement({ drifted: true })], 1, false);
    const mark = await screen.findByText("drifted");
    expect(mark.style.color).not.toBe("");
  });
});
