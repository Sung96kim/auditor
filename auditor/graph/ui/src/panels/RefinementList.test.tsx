import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RefinementList from "./RefinementList";
import type { RefinementRow } from "../api/types";

function refinement(over: Partial<RefinementRow>): RefinementRow {
  return {
    refinement_id: "r1",
    run_id: "run1",
    kind: "edge",
    tier: "call",
    status: "active",
    src: "a.py::one",
    dst: "b.py::two",
    edge_kind: "calls",
    node_id: null,
    from_dst: null,
    reason: "resolved",
    confidence: 0.9,
    drifted: false,
    ...over,
  };
}

function serve(rows: RefinementRow[], refinement_count: number, truncated: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ refinements: { rows, refinement_count, truncated } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
  return render(<RefinementList base="/" repo="/w" />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the refinement list", () => {
  it("a status with rows gets a heading and a count", async () => {
    serve([refinement({})], 1, false);
    expect((await screen.findByText(/active \(1\)/)).textContent).toBe("active (1)");
  });

  it("the five statuses with nothing in them report a zero without each taking a heading", async () => {
    serve([refinement({})], 1, false);
    await screen.findByText(/active \(1\)/);
    for (const status of ["pending", "pinned", "redundant", "rejected", "reverted"]) {
      expect(screen.getByText(`${status} 0`)).not.toBeNull();
      expect(screen.queryByText(`${status} (0)`)).toBeNull();
    }
  });

  it("a truncated list says how many rows it is not showing", async () => {
    serve([refinement({})], 9, true);
    const strip = screen.getByTestId("RefinementList").firstElementChild;
    await screen.findByText(/active \(1\)/);
    expect(strip?.textContent).toBe("Refinements1 of 9");
  });

  it("an untruncated list shows the count alone, with nothing to qualify", async () => {
    serve([refinement({})], 1, false);
    await screen.findByText(/active \(1\)/);
    expect(screen.getByTestId("RefinementList").firstElementChild?.textContent).toBe(
      "Refinements1",
    );
  });

  it("a drifted row is marked in its own tone, not in a parenthesis in the same grey", async () => {
    serve([refinement({ drifted: true })], 1, false);
    const mark = await screen.findByText("drifted");
    expect(mark.style.color).not.toBe("");
  });
});
