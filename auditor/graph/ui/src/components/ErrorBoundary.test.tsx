import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ErrorBoundary from "./ErrorBoundary";

function Throws({ boom }: { boom: boolean }): React.ReactElement {
  if (boom) throw new Error("Sigma: Container has no width.");
  return <span>the graph</span>;
}

/** React logs a caught render error itself, on top of what the boundary reports. */
function quietly(run: () => void): void {
  const original = console.error;
  console.error = vi.fn();
  try {
    run();
  } finally {
    console.error = original;
  }
}

afterEach(cleanup);

describe("what a render throw takes down with it", () => {
  it("the boundary shows the reason rather than a blank page", () => {
    quietly(() =>
      render(
        <ErrorBoundary>
          <Throws boom />
        </ErrorBoundary>,
      ),
    );
    expect(screen.getByText(/Something went wrong rendering the graph/)).not.toBeNull();
    expect(screen.getByText("Sigma: Container has no width.")).not.toBeNull();
  });

  it("a sibling outside the boundary is untouched, which is why the canvas has its own", () => {
    quietly(() =>
      render(
        <div>
          <ErrorBoundary>
            <Throws boom />
          </ErrorBoundary>
          <span>the operator panels</span>
        </div>,
      ),
    );
    expect(screen.getByText("the operator panels")).not.toBeNull();
  });

  it("Try again re-renders the children rather than leaving the box on screen", () => {
    function Page() {
      return (
        <ErrorBoundary>
          <Throws boom={false} />
        </ErrorBoundary>
      );
    }
    quietly(() => {
      const { rerender } = render(
        <ErrorBoundary>
          <Throws boom />
        </ErrorBoundary>,
      );
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      rerender(<Page />);
    });
    expect(screen.getByText("the graph")).not.toBeNull();
  });
});
