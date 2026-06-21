import type { GraphPayload } from "../types";
import type { View } from "./buildGraph";

/** A navigable breadcrumb target: where clicking this crumb should take the view. */
export type CrumbTarget =
  | { kind: "overview" }
  | { kind: "cluster"; clusterId: number };

export interface Crumb {
  label: string;
  target: CrumbTarget;
}

/**
 * Build the breadcrumb trail for the current view + selection.
 *
 *  - overview                  → [Overview]
 *  - cluster view              → [Overview, Cluster "<label>"]
 *  - ego / selected node       → [Overview, Cluster?, <node label>]
 *
 * Each crumb carries a `target` so the caller can navigate back when it is clicked.
 * The final crumb represents the current location; callers typically render it inert.
 */
export function breadcrumbPath(
  view: View,
  payload: GraphPayload,
  selectedNodeId: string | null
): Crumb[] {
  const crumbs: Crumb[] = [{ label: "Overview", target: { kind: "overview" } }];

  const clusterLabel = (clusterId: number): string => {
    const c = payload.clusters.find((cl) => cl.cluster_id === clusterId);
    return c ? c.label : `${clusterId}`;
  };

  const selectedNode = selectedNodeId
    ? payload.nodes.find((n) => n.id === selectedNodeId) ?? null
    : null;

  // Resolve the cluster crumb (when there is one): from the explicit view, the
  // ego node, or — when a node is selected in overview — the selected node itself.
  let clusterId: number | null = null;
  if (view.mode === "cluster") {
    clusterId = view.clusterId;
  } else if (view.mode === "ego") {
    const egoNode = payload.nodes.find((n) => n.id === view.nodeId) ?? null;
    clusterId = egoNode?.cluster ?? selectedNode?.cluster ?? null;
  } else if (selectedNode) {
    clusterId = selectedNode.cluster;
  }

  if (clusterId !== null) {
    crumbs.push({
      label: `Cluster "${clusterLabel(clusterId)}"`,
      target: { kind: "cluster", clusterId },
    });
  }

  if (selectedNode) {
    crumbs.push({
      label: selectedNode.label,
      target:
        selectedNode.cluster !== null
          ? { kind: "cluster", clusterId: selectedNode.cluster }
          : { kind: "overview" },
    });
  }

  return crumbs;
}
