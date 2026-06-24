/**
 * Label hit-testing. Sigma only hit-tests the node circle, so to make a node's
 * label clickable we reconstruct the label's on-screen box (drawn to the right of
 * the circle) and test the click point against it.
 */

export interface LabelBox {
  id: string;
  x0: number;
  x1: number;
  yTop: number;
  yBottom: number;
}

/** Bounding box of a node's label, rendered to the right of the node circle. All
 * inputs are in viewport pixels; mirrors sigma's default label placement. */
export function labelBox(
  id: string,
  vpX: number,
  vpY: number,
  radiusPx: number,
  textWidth: number,
  labelSize: number,
): LabelBox {
  const x0 = vpX + radiusPx + 1;
  const halfH = labelSize * 0.75;
  return { id, x0, x1: x0 + textWidth + 4, yTop: vpY - halfH, yBottom: vpY + halfH };
}

/** The first label box containing the point, or null. Iterates in caller order so
 * the topmost / first-listed node wins on overlap. */
export function nodeAtPoint(px: number, py: number, boxes: LabelBox[]): string | null {
  for (const b of boxes) {
    if (px >= b.x0 && px <= b.x1 && py >= b.yTop && py <= b.yBottom) return b.id;
  }
  return null;
}
