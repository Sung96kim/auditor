import { describe, it, expect } from "vitest";
import { labelBox, nodeAtPoint } from "./labelHit";

describe("labelBox", () => {
  it("places the box just right of the node circle", () => {
    const b = labelBox("n", 100, 50, 8, 40, 13);
    expect(b.x0).toBe(109); // 100 + 8 + 1
    expect(b.x1).toBe(153); // 109 + 40 + 4
    expect(b.yTop).toBeCloseTo(50 - 9.75);
    expect(b.yBottom).toBeCloseTo(50 + 9.75);
  });
});

describe("nodeAtPoint", () => {
  const boxes = [
    labelBox("a", 0, 0, 5, 30, 13),
    labelBox("b", 0, 100, 5, 30, 13),
  ];

  it("returns the node whose label box contains the point", () => {
    expect(nodeAtPoint(20, 0, boxes)).toBe("a");
    expect(nodeAtPoint(20, 100, boxes)).toBe("b");
  });

  it("returns null when the point is outside every label", () => {
    expect(nodeAtPoint(500, 500, boxes)).toBeNull();
    expect(nodeAtPoint(2, 0, boxes)).toBeNull(); // left of the box (inside the circle area)
  });

  it("returns the first match when boxes overlap", () => {
    const overlap = [
      labelBox("a", 0, 0, 5, 100, 13),
      labelBox("b", 0, 0, 5, 100, 13),
    ];
    expect(nodeAtPoint(20, 0, overlap)).toBe("a");
  });
});
