import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Empty, Failed, Loading, Reconnecting } from "./States";

afterEach(cleanup);

describe("the four states every polled surface renders through", () => {
  it("an empty ledger says so and is not an error", () => {
    render(<Empty what="runs" hint="the observer has not started a run for this repo yet" />);
    expect(screen.getByRole("status").textContent).toContain("No runs yet");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("loading names what it is waiting for, so no panel is a bare spinner", () => {
    render(<Loading what="the daemon" />);
    expect(screen.getByRole("status").textContent).toContain("Loading the daemon");
  });

  it("a reconnect carries the error and a retry that fires, never a blank panel", () => {
    const onRetry = vi.fn();
    render(<Reconnecting error="network down" onRetry={onRetry} />);
    expect(screen.getByRole("status").textContent).toContain("network down");
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a failed first poll is an alert with a retry, which is what ends the spinner", () => {
    const onRetry = vi.fn();
    render(<Failed error="network down" onRetry={onRetry} />);
    expect(screen.getByRole("alert").textContent).toContain("Could not reach the observer");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
