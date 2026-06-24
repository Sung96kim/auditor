import { useMemo, useState } from "react";
import type { GraphPayload } from "../types";
import { buildGraphologyGraph, type View } from "../graph/buildGraph";
import { toOutline, toDot, toJson, type TextNode, type TextEdge } from "../graph/textViews";
import { THEME } from "../theme";

type Fmt = "outline" | "dot" | "json";

const FORMATS: { key: Fmt; label: string }[] = [
  { key: "outline", label: "Outline" },
  { key: "dot", label: "DOT" },
  { key: "json", label: "JSON" },
];

interface GraphTextProps {
  payload: GraphPayload;
  view: View;
  selectedNodeId: string | null;
  depth: number;
}

/** TEXT view mode: the same subgraph the canvas shows, rendered as structured text.
 * Scope mirrors the canvas — a selected node → its ego neighbourhood at the depth slider;
 * otherwise the current view (cluster members, or the overview's cluster index). */
export default function GraphText({ payload, view, selectedNodeId, depth }: GraphTextProps) {
  const [fmt, setFmt] = useState<Fmt>("outline");
  const [copied, setCopied] = useState(false);

  const { nodes, edges } = useMemo(() => {
    const textView: View = selectedNodeId
      ? { mode: "ego", nodeId: selectedNodeId, depth }
      : view;
    const g = buildGraphologyGraph(payload, textView);
    const nodeById = new Map(payload.nodes.map((n) => [n.id, n]));
    const clusterById = new Map(payload.clusters.map((c) => [c.cluster_id, c]));
    const outNodes: TextNode[] = g.mapNodes((id, attrs) => {
      const p = nodeById.get(id);
      const cid = attrs.clusterId as number | undefined;
      return {
        id,
        label: (attrs.label as string) ?? id,
        type: id.startsWith("cluster:")
          ? "cluster"
          : ((attrs.kind as string) ?? p?.type ?? "?"),
        line: p?.line,
        module: p?.module,
        rank: (attrs.rank as number) ?? p?.rank ?? 0,
        cluster: p?.cluster ?? null,
        count: cid != null ? clusterById.get(cid)?.member_count : undefined,
      };
    });
    const outEdges: TextEdge[] = g.mapEdges((_e, attrs, src, tgt) => ({
      source: src,
      target: tgt,
      kind: (attrs.kind as string) ?? "",
    }));
    return { nodes: outNodes, edges: outEdges };
  }, [payload, view, selectedNodeId, depth]);

  const text = useMemo(() => {
    if (fmt === "dot") return toDot(nodes, edges);
    if (fmt === "json") return toJson(nodes, edges);
    return toOutline(nodes, edges);
  }, [fmt, nodes, edges]);

  const onCopy = (): void => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  };

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        background: THEME.bgCanvas,
      }}
    >
      {/* centered control pill — clears the top-left Controls + top-right view toggle */}
      <div
        style={{
          position: "absolute",
          top: "12px",
          left: "50%",
          transform: "translateX(-50%)",
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "4px",
          background: "rgba(14,18,27,0.92)",
          backdropFilter: "blur(10px)",
          border: `1px solid ${THEME.border}`,
          borderRadius: "10px",
          zIndex: 6,
        }}
      >
        <div style={{ display: "flex", gap: "2px" }}>
          {FORMATS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFmt(f.key)}
              style={{
                background: fmt === f.key ? THEME.accent : "transparent",
                color: fmt === f.key ? "#0b0e15" : "#94a3b8",
                border: "none",
                borderRadius: "7px",
                cursor: "pointer",
                fontSize: "11px",
                fontWeight: 700,
                letterSpacing: "0.03em",
                padding: "4px 12px",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div style={{ width: "1px", height: "18px", background: THEME.border }} />
        <button
          onClick={onCopy}
          disabled={!text}
          style={{
            background: "transparent",
            color: copied ? "#46C98B" : "#94a3b8",
            border: "none",
            cursor: text ? "pointer" : "default",
            fontSize: "11px",
            fontWeight: 600,
            padding: "4px 10px",
          }}
        >
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>

      {nodes.length === 0 ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#475569",
            fontSize: "13px",
          }}
        >
          Select a node or cluster to see its structure as text.
        </div>
      ) : (
        <pre
          style={{
            position: "absolute",
            inset: 0,
            margin: 0,
            paddingTop: "58px",
            paddingLeft: "18px",
            paddingRight: "18px",
            paddingBottom: "18px",
            overflow: "auto",
            color: "#CBD5E1",
            fontSize: "12.5px",
            lineHeight: 1.55,
            fontFamily:
              'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
            whiteSpace: "pre",
            tabSize: 2,
          }}
        >
          {text}
        </pre>
      )}

      {/* node/edge counts, bottom-left */}
      <div
        style={{
          position: "absolute",
          bottom: "10px",
          left: "14px",
          color: "#475569",
          fontSize: "11px",
          fontFamily: "monospace",
          pointerEvents: "none",
        }}
      >
        {nodes.length} nodes · {edges.length} edges
      </div>
    </div>
  );
}
