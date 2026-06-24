import { describe, it, expect } from "vitest";
import { lerp, clamp, easeInOutCubic } from "./anim";

describe("lerp", () => {
  it("returns a at t=0", () => expect(lerp(2, 10, 0)).toBe(2));
  it("returns b at t=1", () => expect(lerp(2, 10, 1)).toBe(10));
  it("returns midpoint at t=0.5", () => expect(lerp(0, 100, 0.5)).toBe(50));
  it("works with negative values", () => expect(lerp(-10, 10, 0.5)).toBe(0));
});

describe("clamp", () => {
  it("returns lo when x < lo", () => expect(clamp(-5, 0, 1)).toBe(0));
  it("returns hi when x > hi", () => expect(clamp(5, 0, 1)).toBe(1));
  it("returns x when in range", () => expect(clamp(0.5, 0, 1)).toBe(0.5));
});

describe("easeInOutCubic", () => {
  it("maps 0 → 0", () => expect(easeInOutCubic(0)).toBe(0));
  it("maps 1 → 1", () => expect(easeInOutCubic(1)).toBe(1));
  it("maps 0.5 → 0.5 (symmetric)", () => expect(easeInOutCubic(0.5)).toBeCloseTo(0.5));
  it("is slower at start (t=0.1 < 0.1)", () => expect(easeInOutCubic(0.1)).toBeLessThan(0.1));
  it("is monotonically increasing", () => {
    const vals = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1].map(easeInOutCubic);
    for (let i = 1; i < vals.length; i++) {
      expect(vals[i]).toBeGreaterThanOrEqual(vals[i - 1]);
    }
  });
});
