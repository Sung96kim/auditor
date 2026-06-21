import { useEffect, useRef } from "react";
import Sigma from "sigma";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type { GraphPayload } from "../types";
import { THEME } from "../theme";
import { buildGraphologyGraph, type View } from "../graph/buildGraph";

interface GraphCanvasProps {
  payload: GraphPayload;
  view: View;
  onSelect: (nodeId: string) => void;
  onDrill: (clusterId: number) => void;
  overlayOn?: boolean;
}

/** Simple deterministic hash of a string → float in [0, 1] */
function hashToFloat(s: string, seed: number): number {
  let h = seed;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return ((h >>> 0) / 0xffffffff);
}

export default function GraphCanvas({
  payload,
  view,
  onSelect,
  onDrill,
  overlayOn = false,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    const g = buildGraphologyGraph(payload, view);

    g.forEachNode((node) => {
      g.setNodeAttribute(node, "x", (hashToFloat(node, 1234) - 0.5) * 200);
      g.setNodeAttribute(node, "y", (hashToFloat(node, 5678) - 0.5) * 200);
    });

    if (g.order > 0) {
      forceAtlas2.assign(g, {
        iterations: 100,
        settings: {
          gravity: 1,
          scalingRatio: 2,
          slowDown: 10,
        },
      });
    }

    // Build findings set for overlay (keyed on label since graph nodes use id or cluster:N)
    const findingsSet = new Set<string>(
      payload.nodes
        .filter((n) => n.findings.length > 0)
        .map((n) => n.id)
    );

    container.style.backgroundColor = THEME.bgCanvas;

    const sigma = new Sigma(g, container, {
      renderEdgeLabels: false,
      defaultEdgeColor: "#1B2230",
      labelColor: { color: "#E6EDF5" },
      labelSize: 13,
      labelWeight: "600",
      labelFont: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      labelRenderedSizeThreshold: 0,
      nodeReducer: (_node, data) => {
        const hasFinding = overlayOn && findingsSet.has(_node);
        return {
          ...data,
          color: hasFinding ? "#EF4444" : (data.color as string ?? THEME.accent),
          size: hasFinding
            ? ((data.size as number ?? 8) * 1.3)
            : (data.size as number ?? 8),
          label: data.label as string,
          borderColor: hasFinding ? "#EF4444" : (data.color as string ?? THEME.accent),
          borderSize: hasFinding ? 0.4 : 0.15,
        };
      },
    });

    sigma.on("clickNode", ({ node }: { node: string }) => {
      if (node.startsWith("cluster:")) {
        const clusterId = parseInt(node.replace("cluster:", ""), 10);
        onDrill(clusterId);
      } else {
        onSelect(node);
      }
    });

    sigmaRef.current = sigma;

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
  }, [payload, view, onSelect, onDrill, overlayOn]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: "100%",
        backgroundColor: THEME.bgCanvas,
        borderRadius: "0.5rem",
        overflow: "hidden",
      }}
    />
  );
}
