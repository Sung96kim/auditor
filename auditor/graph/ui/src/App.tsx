import { useCallback, useMemo, useState } from "react";
import type { GraphPayload, NodeType } from "./types";
import { THEME } from "./theme";
import { sample } from "./sample";
import GraphCanvas from "./components/GraphCanvas";
import Explorer from "./components/Explorer";
import Controls from "./components/Controls";
import type { FilterState } from "./components/Controls";
import DetailPanel from "./components/DetailPanel";
import type { View } from "./graph/buildGraph";
import { applyFilters } from "./graph/filter";

declare global {
  interface Window {
    __AUDITOR_GRAPH__?: GraphPayload;
  }
}

const ALL_TYPES = new Set<NodeType>(["class", "function", "method", "module"]);

function getDistinctLangs(payload: GraphPayload): string[] {
  const langs = new Set<string>(payload.nodes.map((n) => n.lang));
  return [...langs].sort();
}

function makeDefaultFilters(payload: GraphPayload): FilterState {
  return {
    langs: new Set(getDistinctLangs(payload)),
    types: new Set(ALL_TYPES),
    depth: 1,
    overlayOn: false,
  };
}

export default function App() {
  const data: GraphPayload = window.__AUDITOR_GRAPH__ ?? sample;

  const [view, setView] = useState<View>({ mode: "overview" });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filters, setFilters] = useState<FilterState>(() =>
    makeDefaultFilters(data)
  );

  const availableLangs = useMemo(() => getDistinctLangs(data), [data]);

  /** Filtered payload fed to GraphCanvas and Explorer. */
  const filteredPayload = useMemo<GraphPayload>(() => {
    const { nodes, edges } = applyFilters(data, {
      langs: filters.langs,
      types: filters.types,
      query: searchQuery,
    });
    return { ...data, nodes, edges };
  }, [data, filters.langs, filters.types, searchQuery]);

  /** Effective view: selection no longer drives view; view is the navigation source of truth. */
  const effectiveView = useMemo<View>(() => view, [view]);

  const handleSelect = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
  }, []);

  const handleFocus = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    setView({ mode: "ego", nodeId, depth: filters.depth });
  }, [filters.depth]);

  const handleDrill = useCallback((clusterId: number) => {
    setSelectedNodeId(null);
    setView({ mode: "cluster", clusterId });
  }, []);

  const handleReset = useCallback(() => {
    setView({ mode: "overview" });
    setSelectedNodeId(null);
    setSearchQuery("");
    setFilters(makeDefaultFilters(data));
  }, [data]);

  const handleLangToggle = useCallback((lang: string) => {
    setFilters((prev) => {
      const next = new Set(prev.langs);
      if (next.has(lang)) next.delete(lang);
      else next.add(lang);
      return { ...prev, langs: next };
    });
  }, []);

  const handleTypeToggle = useCallback((type: NodeType) => {
    setFilters((prev) => {
      const next = new Set(prev.types);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return { ...prev, types: next };
    });
  }, []);

  const handleDepthChange = useCallback((depth: number) => {
    setFilters((prev) => ({ ...prev, depth }));
    if (selectedNodeId) {
      setView({ mode: "ego", nodeId: selectedNodeId, depth });
    }
  }, [selectedNodeId]);

  const handleOverlayToggle = useCallback(() => {
    setFilters((prev) => ({ ...prev, overlayOn: !prev.overlayOn }));
  }, []);

  const selectedNode = useMemo(
    () => data.nodes.find((n) => n.id === selectedNodeId) ?? null,
    [data.nodes, selectedNodeId]
  );

  const viewLabel =
    view.mode === "overview"
      ? "Overview"
      : view.mode === "cluster"
      ? `Cluster ${(view as Extract<View, { mode: "cluster" }>).clusterId}`
      : `Ego: ${(view as Extract<View, { mode: "ego" }>).nodeId}`;

  const sidebarWidth = 264;
  const detailWidth = 280;

  return (
    <div
      style={{
        height: "100%",
        width: "100%",
        backgroundColor: THEME.bgApp,
        color: "#e2e8f0",
        fontFamily: "monospace",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* ── Header ── */}
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
          {viewLabel} · {data.nodes.length} nodes · {data.edges.length} edges
        </span>
        {view.mode !== "overview" && (
          <button
            onClick={handleReset}
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
        <button
          onClick={handleReset}
          style={{
            background: THEME.bgElevated,
            border: `1px solid ${THEME.border}`,
            color: THEME.accent,
            padding: "0.3rem 0.75rem",
            borderRadius: "0.3rem",
            cursor: "pointer",
            fontSize: "0.8rem",
            fontFamily: "monospace",
          }}
        >
          Reset
        </button>
      </div>

      {/* ── Body ── */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>

        {/* ── Left sidebar: Explorer ── */}
        <div
          style={{
            width: sidebarWidth,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            backgroundColor: THEME.bgPanel,
            borderRight: `1px solid ${THEME.border}`,
            minHeight: 0,
          }}
        >
          {/* Section label */}
          <div
            style={{
              padding: "12px 14px 8px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                fontSize: "11px",
                fontWeight: 600,
                letterSpacing: ".8px",
                color: "#64748b",
              }}
            >
              EXPLORER
            </span>
            <span
              style={{
                fontSize: "11px",
                color: "#64748b",
                fontFamily: "monospace",
              }}
            >
              {filteredPayload.nodes.length} nodes
            </span>
          </div>

          {/* Explorer search + list */}
          <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <Explorer
              nodes={filteredPayload.nodes}
              query={searchQuery}
              onQueryChange={setSearchQuery}
              onSelect={handleSelect}
              selectedNodeId={selectedNodeId}
            />
          </div>
        </div>

        {/* ── Canvas ── */}
        <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
          <GraphCanvas
            payload={filteredPayload}
            view={effectiveView}
            onSelect={handleSelect}
            onDrill={handleDrill}
            onFocus={handleFocus}
            selectedNodeId={selectedNodeId}
            overlayOn={filters.overlayOn}
          />

          {/* NODE TYPES legend overlay (top-left of canvas) */}
          <div
            style={{
              position: "absolute",
              top: "14px",
              left: "14px",
              background: "rgba(14,18,27,0.88)",
              backdropFilter: "blur(10px)",
              border: `1px solid ${THEME.border}`,
              borderRadius: "11px",
              padding: "11px 12px",
              zIndex: 10,
            }}
          >
            <Controls
              availableLangs={availableLangs}
              filters={filters}
              onLangToggle={handleLangToggle}
              onTypeToggle={handleTypeToggle}
              onDepthChange={handleDepthChange}
              onOverlayToggle={handleOverlayToggle}
              onReset={handleReset}
            />
          </div>

          {/* Hint line */}
          <div
            style={{
              position: "absolute",
              bottom: "14px",
              left: "50%",
              transform: "translateX(-50%)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              color: "#374151",
              fontSize: "11.5px",
              pointerEvents: "none",
              zIndex: 5,
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            >
              <circle cx="11" cy="11" r="7" />
              <circle cx="11" cy="11" r="2.5" />
            </svg>
            Click to select · double-click to focus · drag to move · scroll to zoom
          </div>
        </div>

        {/* ── Right panel: Detail ── */}
        <div
          style={{
            width: detailWidth,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            backgroundColor: THEME.bgPanel,
            borderLeft: `1px solid ${THEME.border}`,
            minHeight: 0,
          }}
        >
          <div
            style={{
              padding: "12px 14px 8px",
              flexShrink: 0,
              borderBottom: `1px solid ${THEME.border}`,
            }}
          >
            <span
              style={{
                fontSize: "11px",
                fontWeight: 600,
                letterSpacing: ".8px",
                color: "#64748b",
              }}
            >
              NODE DETAIL
            </span>
          </div>
          <DetailPanel
            node={selectedNode}
            allNodes={data.nodes}
            edges={data.edges}
            onSelectNeighbor={handleSelect}
            onFocus={handleFocus}
          />
        </div>
      </div>

      {/* ── Footer ── */}
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
