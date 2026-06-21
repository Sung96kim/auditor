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
}

/** Simple deterministic hash of a string → float in [0, 1] */
function hashToFloat(s: string, seed: number): number {
  let h = seed;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  // Convert signed int32 to [0,1]
  return ((h >>> 0) / 0xffffffff);
}

export default function GraphCanvas({ payload, view, onSelect, onDrill }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Kill previous sigma instance
    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    const g = buildGraphologyGraph(payload, view);

    // Assign seeded initial positions before layout
    g.forEachNode((node) => {
      g.setNodeAttribute(node, "x", (hashToFloat(node, 1234) - 0.5) * 200);
      g.setNodeAttribute(node, "y", (hashToFloat(node, 5678) - 0.5) * 200);
    });

    // Run forceAtlas2 for fixed iterations (only if graph has nodes)
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

    container.style.backgroundColor = THEME.bgCanvas;

    const sigma = new Sigma(g, container, {
      renderEdgeLabels: false,
      defaultEdgeColor: "#1B2230",
      labelColor: { color: "#c8d3e0" },
      labelSize: 11,
      nodeReducer: (_node, data) => ({
        ...data,
        color: data.color as string ?? THEME.accent,
        size: data.size as number ?? 8,
        label: data.label as string,
        borderColor: (data.color as string ?? THEME.accent),
        borderSize: 0.15,
      }),
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
  }, [payload, view, onSelect, onDrill]);

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
