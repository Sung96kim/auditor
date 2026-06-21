import { useCallback, useState } from "react";
import type { GraphPayload } from "./types";
import { THEME } from "./theme";
import { sample } from "./sample";
import GraphCanvas from "./components/GraphCanvas";
import type { View } from "./graph/buildGraph";

declare global {
  interface Window {
    __AUDITOR_GRAPH__?: GraphPayload;
  }
}

export default function App() {
  const data: GraphPayload = window.__AUDITOR_GRAPH__ ?? sample;
  const { meta } = data;

  const [view, setView] = useState<View>({ mode: "overview" });

  const handleSelect = useCallback((nodeId: string) => {
    setView({ mode: "ego", nodeId, depth: 1 });
  }, []);

  const handleDrill = useCallback((clusterId: number) => {
    setView({ mode: "cluster", clusterId });
  }, []);

  const handleBack = useCallback(() => {
    if (view.mode === "cluster") {
      setView({ mode: "overview" });
    } else if (view.mode === "ego") {
      const node = data.nodes.find((n) => n.id === view.nodeId);
      if (node && node.cluster !== null) {
        setView({ mode: "cluster", clusterId: node.cluster });
      } else {
        setView({ mode: "overview" });
      }
    }
  }, [view, data.nodes]);

  const viewLabel =
    view.mode === "overview"
      ? "Overview"
      : view.mode === "cluster"
      ? `Cluster ${view.clusterId}`
      : `Ego: ${view.nodeId}`;

  return (
    <div
      style={{
        height: "100vh",
        backgroundColor: THEME.bgApp,
        color: "#e2e8f0",
        fontFamily: "monospace",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "1rem",
          padding: "0.75rem 1.25rem",
          backgroundColor: THEME.bgPanel,
          borderBottom: `1px solid ${THEME.border}`,
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            color: THEME.accent,
            margin: 0,
            fontSize: "1rem",
            letterSpacing: "0.05em",
          }}
        >
          Auditor — Codebase Graph
        </h1>
        <span
          style={{
            color: "#64748b",
            fontSize: "0.8rem",
            flexGrow: 1,
          }}
        >
          {viewLabel} · theme: {meta.theme} · accent: {meta.accent} · node_cap: {meta.node_cap}
        </span>
        {view.mode !== "overview" && (
          <button
            onClick={handleBack}
            style={{
              background: THEME.bgElevated,
              border: `1px solid ${THEME.border}`,
              color: "#e2e8f0",
              padding: "0.3rem 0.75rem",
              borderRadius: "0.3rem",
              cursor: "pointer",
              fontSize: "0.8rem",
              fontFamily: "monospace",
            }}
          >
            ← Back
          </button>
        )}
      </div>

      {/* Canvas area */}
      <div style={{ flexGrow: 1, position: "relative", overflow: "hidden" }}>
        <GraphCanvas
          payload={data}
          view={view}
          onSelect={handleSelect}
          onDrill={handleDrill}
        />
      </div>

      {/* Mode pills */}
      <div
        style={{
          display: "flex",
          gap: "0.5rem",
          padding: "0.5rem 1.25rem",
          backgroundColor: THEME.bgPanel,
          borderTop: `1px solid ${THEME.border}`,
          flexShrink: 0,
        }}
      >
        {(["overview", "cluster", "ego"] as const).map((m) => (
          <span
            key={m}
            style={{
              fontSize: "0.7rem",
              padding: "0.2rem 0.6rem",
              borderRadius: "1rem",
              backgroundColor: view.mode === m ? THEME.accent : THEME.bgElevated,
              color: view.mode === m ? "#fff" : "#64748b",
              border: `1px solid ${view.mode === m ? THEME.accent : THEME.border}`,
            }}
          >
            {m}
          </span>
        ))}
        <span style={{ fontSize: "0.7rem", color: "#374151", marginLeft: "auto" }}>
          {window.__AUDITOR_GRAPH__ ? "live" : "sample fixture"}
        </span>
      </div>
    </div>
  );
}
