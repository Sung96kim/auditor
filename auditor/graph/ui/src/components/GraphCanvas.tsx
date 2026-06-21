import { useEffect, useRef } from "react";
import Sigma from "sigma";
import type { NodeHoverDrawingFunction } from "sigma/rendering";
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

const drawDarkNodeHover: NodeHoverDrawingFunction = (context, data, settings) => {
  const size = settings.labelSize;
  const font = settings.labelFont;
  const weight = settings.labelWeight;
  context.font = `${weight} ${size}px ${font}`;

  context.fillStyle = "#161C28";
  context.shadowOffsetX = 0;
  context.shadowOffsetY = 0;
  context.shadowBlur = 8;
  context.shadowColor = "#000";
  const PADDING = 4;
  if (typeof data.label === "string" && data.label) {
    const textWidth = context.measureText(data.label).width;
    const boxWidth = Math.round(textWidth + 9);
    const boxHeight = Math.round(size + 2 * PADDING);
    const radius = Math.max(data.size, size / 2) + PADDING;
    const angleRadian = Math.asin(boxHeight / 2 / radius);
    const xDeltaCoord = Math.sqrt(Math.abs(radius ** 2 - (boxHeight / 2) ** 2));
    context.beginPath();
    context.moveTo(data.x + xDeltaCoord, data.y + boxHeight / 2);
    context.lineTo(data.x + radius + boxWidth, data.y + boxHeight / 2);
    context.lineTo(data.x + radius + boxWidth, data.y - boxHeight / 2);
    context.lineTo(data.x + xDeltaCoord, data.y - boxHeight / 2);
    context.arc(data.x, data.y, radius, angleRadian, -angleRadian);
    context.closePath();
    context.fill();
  } else {
    context.beginPath();
    context.arc(data.x, data.y, data.size + PADDING, 0, Math.PI * 2);
    context.closePath();
    context.fill();
  }
  context.shadowBlur = 0;

  context.fillStyle = data.color;
  context.beginPath();
  context.arc(data.x, data.y, data.size, 0, Math.PI * 2);
  context.closePath();
  context.fill();

  if (typeof data.label === "string" && data.label) {
    context.fillStyle = "#E6EDF5";
    context.fillText(data.label, data.x + data.size + 3, data.y + size / 3);
  }
};

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
      defaultDrawNodeHover: drawDarkNodeHover,
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
