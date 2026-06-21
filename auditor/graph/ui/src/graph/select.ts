import type { GraphPayload } from "../types";

/** Returns the set of direct neighbor node IDs (both directions) for a given nodeId. */
export function neighborIds(payload: GraphPayload, nodeId: string): Set<string> {
  const result = new Set<string>();
  for (const edge of payload.edges) {
    if (edge.source === nodeId) {
      result.add(edge.target);
    } else if (edge.target === nodeId) {
      result.add(edge.source);
    }
  }
  return result;
}
