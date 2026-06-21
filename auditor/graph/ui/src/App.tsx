import type { GraphPayload } from "./types";
import { THEME } from "./theme";
import { sample } from "./sample";

declare global {
  interface Window {
    __AUDITOR_GRAPH__?: GraphPayload;
  }
}

export default function App() {
  const data: GraphPayload = window.__AUDITOR_GRAPH__ ?? sample;
  const { nodes, edges, clusters, meta } = data;

  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: THEME.bgApp,
        color: "#e2e8f0",
        fontFamily: "monospace",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1.5rem",
        padding: "2rem",
      }}
    >
      <h1 style={{ color: THEME.accent, margin: 0, fontSize: "1.5rem", letterSpacing: "0.05em" }}>
        Auditor — Codebase Graph
      </h1>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: "1rem",
          width: "100%",
          maxWidth: "600px",
        }}
      >
        <StatCard label="Nodes" value={nodes.length} bg={THEME.bgPanel} border={THEME.border} />
        <StatCard label="Edges" value={edges.length} bg={THEME.bgPanel} border={THEME.border} />
        <StatCard label="Clusters" value={clusters.length} bg={THEME.bgPanel} border={THEME.border} />
      </div>
      <p style={{ color: "#64748b", margin: 0, fontSize: "0.8rem" }}>
        theme: {meta.theme} · accent: {meta.accent} · node_cap: {meta.node_cap}
      </p>
      <p style={{ color: "#374151", margin: 0, fontSize: "0.75rem" }}>
        {window.__AUDITOR_GRAPH__ ? "data: window.__AUDITOR_GRAPH__" : "data: sample fixture (dev)"}
      </p>
    </div>
  );
}

function StatCard({
  label,
  value,
  bg,
  border,
}: {
  label: string;
  value: number;
  bg: string;
  border: string;
}) {
  return (
    <div
      style={{
        backgroundColor: bg,
        border: `1px solid ${border}`,
        borderRadius: "0.5rem",
        padding: "1rem",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: "1.75rem", fontWeight: "bold", color: "#f1f5f9" }}>{value}</div>
      <div style={{ fontSize: "0.75rem", color: "#64748b", marginTop: "0.25rem" }}>{label}</div>
    </div>
  );
}
