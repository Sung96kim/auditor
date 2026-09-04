import type { GraphPayload } from "../types";

/** Spec 12.1: a node a refinement touched is highlighted, and the overlay says which. */
export function refinedNodeIds(payload: GraphPayload): Set<string> {
  return new Set(payload.nodes.filter((n) => n.refined === true).map((n) => n.id));
}

/** A unit separator: node ids can carry a space, which would let two edges share one key. */
const SEP = "\u001f";

export function edgeKey(source: string, target: string, kind: string): string {
  return [source, target, kind].join(SEP);
}

function overlay(payload: GraphPayload) {
  return payload.edges.filter((e) => e.provenance === "refined");
}

/** An edge the refiner added, which is `provenance` naming the refined pass rather than the walk. */
export function refinedEdgeKeys(payload: GraphPayload): Set<string> {
  return new Set(overlay(payload).map((e) => edgeKey(e.source, e.target, e.kind)));
}

/** An overlay edge nobody confirmed is drawn provisionally, which is what `confirmed` is for. */
export function unconfirmedEdgeKeys(payload: GraphPayload): Set<string> {
  return new Set(
    overlay(payload)
      .filter((e) => !e.confirmed)
      .map((e) => edgeKey(e.source, e.target, e.kind)),
  );
}

/** Every edge type the canvas reducer can return, so `GraphCanvas` registers a program for each.
 *
 * Sigma looks the reducer's `type` up in `edgeProgramClasses` and throws inside its render loop
 * when it finds nothing, taking the whole canvas down with it; the map it is given replaces
 * sigma's defaults wholesale, so an unregistered name is not a fallback, it is a crash.
 */
export const EDGE_TYPES = { drawn: "line", provisional: "provisional" } as const;

/** The style an overlay edge is drawn in. Sigma ships no dashed program, so an unconfirmed
 * edge is the same curve without its arrowhead, which is a distinction the renderer supports. */
export function refinedEdgeType(confirmed: boolean): string {
  return confirmed ? EDGE_TYPES.drawn : EDGE_TYPES.provisional;
}
