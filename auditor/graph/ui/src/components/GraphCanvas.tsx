import { useEffect, useRef } from "react";
import Sigma from "sigma";
import type { NodeHoverDrawingFunction } from "sigma/rendering";
import { animateNodes } from "sigma/utils";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type Graph from "graphology";
import { createNodeBorderProgram } from "@sigma/node-border";
import EdgeCurveProgram from "@sigma/edge-curve";
import type { GraphPayload } from "../types";
import { THEME } from "../theme";
import { buildGraphologyGraph, type View } from "../graph/buildGraph";
import { easeInOutCubic, lerp, makeTween, tickTween, type TweenState } from "../graph/anim";

const prefersReducedMotion =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

interface GraphCanvasProps {
  payload: GraphPayload;
  view: View;
  onSelect: (nodeId: string) => void;
  onDrill: (clusterId: number) => void;
  onFocus: (nodeId: string) => void;
  selectedNodeId?: string | null;
  overlayOn?: boolean;
}

interface SelectionState {
  id: string | null;
  neighbors: Set<string>;
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

function hashToFloat(s: string, seed: number): number {
  let h = seed;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return (h >>> 0) / 0xffffffff;
}

function hexAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function alphaColor(color: string, alpha: number): string {
  if (alpha >= 1) return color;
  if (color.startsWith("#") && color.length === 7) {
    return hexAlpha(color, alpha);
  }
  return color;
}

function blendHex(from: string, to: string, t: number): string {
  if (t >= 1) return to;
  if (t <= 0) return from;
  const fr = parseInt(from.slice(1, 3), 16);
  const fg = parseInt(from.slice(3, 5), 16);
  const fb = parseInt(from.slice(5, 7), 16);
  const tr = parseInt(to.slice(1, 3), 16);
  const tg = parseInt(to.slice(3, 5), 16);
  const tb = parseInt(to.slice(5, 7), 16);
  const r = Math.round(lerp(fr, tr, t));
  const gg = Math.round(lerp(fg, tg, t));
  const b = Math.round(lerp(fb, tb, t));
  return `rgb(${r},${gg},${b})`;
}

export default function GraphCanvas({
  payload,
  view,
  onSelect,
  onDrill,
  onFocus,
  selectedNodeId = null,
  overlayOn = false,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);

  const onSelectRef = useRef(onSelect);
  const onDrillRef = useRef(onDrill);
  const onFocusRef = useRef(onFocus);
  onSelectRef.current = onSelect;
  onDrillRef.current = onDrill;
  onFocusRef.current = onFocus;

  const selectionRef = useRef<SelectionState>({ id: null, neighbors: new Set() });

  const entranceTweenRef = useRef<TweenState>({ active: false, startTime: 0, duration: 0, progress: 1 });
  const selectionTweenRef = useRef<TweenState>({ active: false, startTime: 0, duration: 150, progress: 1 });
  const rafRef = useRef<number | null>(null);
  const findingsSetRef = useRef<Set<string>>(new Set());
  const overlayOnRef = useRef<boolean>(overlayOn);
  overlayOnRef.current = overlayOn;

  function stopRaf(): void {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }

  function scheduleRaf(): void {
    if (rafRef.current !== null) return;
    function tick(): void {
      const sigma = sigmaRef.current;
      if (!sigma) {
        rafRef.current = null;
        return;
      }
      const now = performance.now();
      if (entranceTweenRef.current.active) {
        entranceTweenRef.current = tickTween(entranceTweenRef.current, now);
      }
      if (selectionTweenRef.current.active) {
        selectionTweenRef.current = tickTween(selectionTweenRef.current, now);
      }
      sigma.refresh();
      const needsPulse = overlayOnRef.current && findingsSetRef.current.size > 0;
      const anyActive = entranceTweenRef.current.active || selectionTweenRef.current.active || needsPulse;
      if (anyActive) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        rafRef.current = null;
      }
    }
    rafRef.current = requestAnimationFrame(tick);
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const oldPositions = new Map<string, { x: number; y: number }>();
    if (graphRef.current) {
      graphRef.current.forEachNode((node, attrs) => {
        oldPositions.set(node, { x: attrs.x as number, y: attrs.y as number });
      });
    }

    if (sigmaRef.current) {
      stopRaf();
      sigmaRef.current.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    }

    const g = buildGraphologyGraph(payload, view);
    graphRef.current = g;

    g.forEachNode((node) => {
      g.setNodeAttribute(node, "x", (hashToFloat(node, 1234) - 0.5) * 200);
      g.setNodeAttribute(node, "y", (hashToFloat(node, 5678) - 0.5) * 200);
    });

    if (g.order > 0) {
      forceAtlas2.assign(g, {
        iterations: 100,
        settings: { gravity: 1, scalingRatio: 2, slowDown: 10 },
      });
    }

    const findingsSet = new Set<string>(
      payload.nodes.filter((n) => n.findings.length > 0).map((n) => n.id)
    );
    findingsSetRef.current = findingsSet;

    const targetPositions: Record<string, { x: number; y: number }> = {};
    g.forEachNode((node, attrs) => {
      targetPositions[node] = { x: attrs.x as number, y: attrs.y as number };
    });

    if (!prefersReducedMotion && oldPositions.size > 0) {
      g.forEachNode((node) => {
        if (oldPositions.has(node)) {
          const old = oldPositions.get(node)!;
          g.setNodeAttribute(node, "x", old.x);
          g.setNodeAttribute(node, "y", old.y);
        }
      });
    }

    container.style.backgroundColor = THEME.bgCanvas;

    const NodeBorderProg = createNodeBorderProgram({
      borders: [
        { size: { attribute: "borderSize", defaultValue: 0.15 }, color: { attribute: "borderColor", defaultValue: THEME.accent } },
        { size: { fill: true }, color: { attribute: "color", defaultValue: THEME.accent } },
      ],
    });

    const sigma = new Sigma(g, container, {
      renderEdgeLabels: false,
      defaultEdgeColor: "#1B2230",
      labelColor: { color: "#E6EDF5" },
      labelSize: 13,
      labelWeight: "600",
      labelFont: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      labelRenderedSizeThreshold: 0,
      defaultDrawNodeHover: drawDarkNodeHover,
      nodeProgramClasses: { circle: NodeBorderProg },
      edgeProgramClasses: { line: EdgeCurveProgram },
      nodeReducer: (node, data) => {
        const ep = easeInOutCubic(entranceTweenRef.current.progress);
        const sp = easeInOutCubic(selectionTweenRef.current.progress);
        const hasFinding = overlayOn && findingsSet.has(node);
        const sel = selectionRef.current;

        const pulseAmp = 0.15;
        const pulseHz = 1 / 2000;
        const pulse = overlayOnRef.current && findingsSet.has(node)
          ? 1 + pulseAmp * Math.sin(2 * Math.PI * pulseHz * performance.now())
          : 1;

        let resolved: Record<string, unknown>;

        if (sel.id !== null) {
          const baseSize = (data.size as number) ?? 8;
          if (node === sel.id) {
            resolved = {
              ...data,
              color: THEME.accent,
              size: baseSize * 1.25,
              label: data.label as string,
              borderColor: THEME.accent,
              borderSize: 0.5,
            };
          } else if (sel.neighbors.has(node)) {
            const finalColor = hasFinding ? "#EF4444" : (data.color as string ?? THEME.accent);
            resolved = {
              ...data,
              color: finalColor,
              size: hasFinding ? baseSize * 1.3 : baseSize,
              label: data.label as string,
              borderColor: finalColor,
              borderSize: 0.15,
            };
          } else {
            const dimColor = "#2A3344";
            const baseColor = (data.color as string | undefined) ?? THEME.accent;
            const blendedColor = baseColor.startsWith("#") && baseColor.length === 7
              ? blendHex(baseColor, dimColor, sp)
              : dimColor;
            resolved = {
              ...data,
              color: blendedColor,
              size: lerp(baseSize, baseSize * 0.7, sp),
              label: sp > 0.5 ? "" : data.label as string,
              borderColor: blendHex(baseColor.startsWith("#") && baseColor.length === 7 ? baseColor : dimColor, dimColor, sp),
              borderSize: 0.1,
            };
          }
        } else {
          const baseSize = (data.size as number) ?? 8;
          const finalColor = hasFinding ? "#EF4444" : (data.color as string ?? THEME.accent);
          resolved = {
            ...data,
            color: finalColor,
            size: hasFinding ? baseSize * 1.3 * pulse : baseSize,
            label: data.label as string,
            borderColor: finalColor,
            borderSize: hasFinding ? 0.4 : 0.15,
          };
        }

        return {
          ...resolved,
          size: lerp(0, resolved.size as number, ep),
          color: alphaColor(resolved.color as string, ep),
          borderColor: alphaColor(resolved.borderColor as string, ep),
        };
      },
      edgeReducer: (edge, data) => {
        const sel = selectionRef.current;
        const rawWeight = (data.weight as number | undefined) ?? 1;
        const baseAlpha = Math.min(0.12 + (rawWeight / 10) * 0.43, 0.55);

        if (sel.id === null) {
          return { ...data, color: hexAlpha("#4A5568", baseAlpha) };
        }
        const src = g.source(edge);
        const tgt = g.target(edge);
        if (src === sel.id || tgt === sel.id) {
          return { ...data, color: THEME.accent + "66", hidden: false };
        }
        return { ...data, hidden: true };
      },
    });

    sigma.on("clickNode", ({ node }: { node: string }) => {
      if (node.startsWith("cluster:")) {
        const clusterId = parseInt(node.replace("cluster:", ""), 10);
        onDrillRef.current(clusterId);
      } else {
        onSelectRef.current(node);
      }
    });

    sigma.on("doubleClickNode", (e: { node: string; preventSigmaDefault?: () => void }) => {
      if (e.preventSigmaDefault) e.preventSigmaDefault();
      if (!e.node.startsWith("cluster:")) {
        onFocusRef.current(e.node);
      }
    });

    sigmaRef.current = sigma;

    const camera = sigma.getCamera();
    camera.animatedReset({ duration: 400 });

    if (!prefersReducedMotion && Object.keys(targetPositions).length > 0) {
      const morphTargets: Record<string, { x: number; y: number }> = {};
      Object.entries(targetPositions).forEach(([node, pos]) => {
        if (oldPositions.has(node)) {
          morphTargets[node] = pos;
        }
      });
      if (Object.keys(morphTargets).length > 0) {
        animateNodes(g, morphTargets, { duration: 300, easing: "cubicInOut" }, () => {
          sigma.refresh();
        });
      }
    }

    if (prefersReducedMotion) {
      entranceTweenRef.current = { active: false, startTime: 0, duration: 250, progress: 1 };
    } else {
      entranceTweenRef.current = makeTween(250);
      scheduleRaf();
    }

    return () => {
      stopRaf();
      sigma.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
  }, [payload, view, overlayOn]);

  useEffect(() => {
    const sigma = sigmaRef.current;
    const g = graphRef.current;
    if (!sigma || !g) return;

    const neighbors = new Set<string>(
      selectedNodeId && g.hasNode(selectedNodeId) ? g.neighbors(selectedNodeId) : []
    );
    selectionRef.current = { id: selectedNodeId, neighbors };

    if (!prefersReducedMotion) {
      selectionTweenRef.current = makeTween(150);
      scheduleRaf();
    } else {
      selectionTweenRef.current = { active: false, startTime: 0, duration: 150, progress: 1 };
      sigma.refresh();
    }
  }, [selectedNodeId]);

  useEffect(() => {
    overlayOnRef.current = overlayOn;
    if (overlayOn && findingsSetRef.current.size > 0 && !prefersReducedMotion) {
      scheduleRaf();
    }
  }, [overlayOn]);

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        position: "relative",
        borderRadius: "0.5rem",
        overflow: "hidden",
      }}
    >
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height: "100%",
          backgroundColor: THEME.bgCanvas,
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "radial-gradient(ellipse at 50% 50%, transparent 45%, rgba(0,0,0,0.55) 100%)",
          pointerEvents: "none",
          borderRadius: "0.5rem",
        }}
      />
    </div>
  );
}
