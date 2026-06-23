import { useCallback, useMemo, useState } from "react";
import type { GraphPayload, NodeType } from "./types";
import { THEME } from "./theme";
import { sample } from "./sample";
import GraphCanvas from "./components/GraphCanvas";
import Graph3D from "./components/Graph3D";
import GraphText from "./components/GraphText";
import Explorer from "./components/Explorer";
import TypeFilter from "./components/TypeFilter";
import Controls from "./components/Controls";
import type { FilterState } from "./components/Controls";
import DetailPanel from "./components/DetailPanel";
import TopBar from "./components/TopBar";
import type { View } from "./graph/buildGraph";
import { applyFilters } from "./graph/filter";
import { breadcrumbPath, type CrumbTarget } from "./graph/breadcrumb";

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

/** Uppercase, letter-spaced section header shared across all panels. */
function SectionHeader({
  label,
  trailing,
}: {
  label: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "8px",
      }}
    >
      <span
        style={{
          fontSize: "10.5px",
          fontWeight: 700,
          letterSpacing: "0.09em",
          color: "#64748b",
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      {trailing}
    </div>
  );
}

const collapseBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#64748b",
  cursor: "pointer",
  fontSize: "16px",
  lineHeight: 1,
  padding: "0 2px",
};

export default function App() {
  const data: GraphPayload = window.__AUDITOR_GRAPH__ ?? sample;

  const [view, setView] = useState<View>({ mode: "overview" });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [controlsOpen, setControlsOpen] = useState(true);
  const [dim, setDim] = useState<"2d" | "3d" | "text">("2d");
  const [filters, setFilters] = useState<FilterState>(() =>
    makeDefaultFilters(data)
  );

  const availableLangs = useMemo(() => getDistinctLangs(data), [data]);

  /** Payload fed to the canvas + Explorer. Deliberately NOT filtered by the search query —
   * the search only narrows the Explorer LIST (which filters itself); live-filtering the canvas
   * rebuilt sigma on every keystroke (flicker). Only lang/type filters reshape the canvas. */
  const filteredPayload = useMemo<GraphPayload>(() => {
    const { nodes, edges } = applyFilters(data, {
      langs: filters.langs,
      types: filters.types,
      query: "",
    });
    return { ...data, nodes, edges };
  }, [data, filters.langs, filters.types]);

  const handleSelect = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
  }, []);

  const handleBackground = useCallback(() => {
    setSelectedNodeId(null); // click empty canvas / Esc → deselect (un-isolate)
  }, []);

  const handleFocus = useCallback(
    (nodeId: string) => {
      setSelectedNodeId(nodeId);
      setView({ mode: "ego", nodeId, depth: filters.depth });
    },
    [filters.depth]
  );

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

  const handleCrumb = useCallback(
    (target: CrumbTarget) => {
      if (target.kind === "overview") {
        setView({ mode: "overview" });
        setSelectedNodeId(null);
      } else {
        setView({ mode: "cluster", clusterId: target.clusterId });
        setSelectedNodeId(null);
      }
    },
    []
  );

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

  const handleDepthChange = useCallback(
    (depth: number) => {
      setFilters((prev) => ({ ...prev, depth }));
      if (selectedNodeId) {
        setView({ mode: "ego", nodeId: selectedNodeId, depth });
      }
    },
    [selectedNodeId]
  );

  const handleOverlayToggle = useCallback(() => {
    setFilters((prev) => ({ ...prev, overlayOn: !prev.overlayOn }));
  }, []);

  const selectedNode = useMemo(
    () => data.nodes.find((n) => n.id === selectedNodeId) ?? null,
    [data.nodes, selectedNodeId]
  );

  const crumbs = useMemo(
    () => breadcrumbPath(view, data, selectedNodeId),
    [view, data, selectedNodeId]
  );

  const title = data.meta.repo ?? "Codebase Graph";

  const sidebarWidth = 268;
  const detailWidth = 288;

  return (
    <div
      style={{
        height: "100%",
        width: "100%",
        backgroundColor: THEME.bgApp,
        color: "#e2e8f0",
        fontFamily:
          'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <TopBar
        title={title}
        crumbs={crumbs}
        nodeCount={data.nodes.length}
        edgeCount={data.edges.length}
        clusterCount={data.clusters.length}
        onCrumb={handleCrumb}
      />

      {/* ── Body ── */}
      <div style={{ flex: 1, display: "flex", minHeight: 0, gap: "12px", padding: "12px" }}>
        {/* ── Left: Explorer panel ── */}
        <div
          className="anim-sidebar"
          style={{
            position: "relative",
            width: sidebarOpen ? sidebarWidth : 34,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            backgroundColor: THEME.bgPanel,
            border: `1px solid ${THEME.border}`,
            borderRadius: "12px",
            minHeight: 0,
            overflow: "hidden",
            transition: "width 240ms cubic-bezier(0.4,0,0.2,1)",
          }}
        >
          {/* full content, fixed width so it doesn't reflow while the panel animates; fades out */}
          <div
            style={{
              width: sidebarWidth,
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              opacity: sidebarOpen ? 1 : 0,
              pointerEvents: sidebarOpen ? "auto" : "none",
              transition: "opacity 160ms ease",
            }}
          >
            <div
              style={{
                padding: "12px 14px 10px",
                flexShrink: 0,
                borderBottom: `1px solid ${THEME.border}`,
              }}
            >
              <SectionHeader
                label="Explorer"
                trailing={
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span
                      style={{
                        fontSize: "11px",
                        color: "#64748b",
                        fontFamily: "monospace",
                      }}
                    >
                      {filteredPayload.nodes.length} nodes
                    </span>
                    <button
                      onClick={() => setSidebarOpen(false)}
                      title="Collapse"
                      className="collapse-btn"
                      style={collapseBtnStyle}
                    >
                      ‹
                    </button>
                  </div>
                }
              />
            </div>
            <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <TypeFilter types={filters.types} onToggle={handleTypeToggle} />
              <Explorer
                nodes={filteredPayload.nodes}
                query={searchQuery}
                onQueryChange={setSearchQuery}
                onSelect={handleFocus}
                selectedNodeId={selectedNodeId}
              />
            </div>
          </div>
          {/* expand affordance — overlays when collapsed, fades in */}
          <button
            onClick={() => setSidebarOpen(true)}
            title="Expand Explorer"
            className="collapse-btn"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "transparent",
              border: "none",
              color: "#94a3b8",
              cursor: "pointer",
              fontSize: "15px",
              opacity: sidebarOpen ? 0 : 1,
              pointerEvents: sidebarOpen ? "none" : "auto",
              transition: "opacity 160ms ease",
            }}
          >
            ›
          </button>
        </div>

        {/* ── Canvas ── */}
        <div
          style={{
            flex: 1,
            position: "relative",
            minHeight: 0,
            borderRadius: "12px",
            overflow: "hidden",
            border: `1px solid ${THEME.border}`,
          }}
        >
          {dim === "text" ? (
            <GraphText
              payload={filteredPayload}
              view={view}
              selectedNodeId={selectedNodeId}
              depth={filters.depth}
            />
          ) : dim === "3d" ? (
            <Graph3D
              payload={filteredPayload}
              onSelect={handleSelect}
              onBackground={handleBackground}
            />
          ) : (
            <GraphCanvas
              payload={filteredPayload}
              view={view}
              onSelect={handleSelect}
              onDrill={handleDrill}
              onFocus={handleFocus}
              onBackground={handleBackground}
              selectedNodeId={selectedNodeId}
              overlayOn={filters.overlayOn}
            />
          )}

          {/* 2D / 3D view toggle (top-right) */}
          <div
            style={{
              position: "absolute",
              top: "12px",
              right: "12px",
              display: "flex",
              gap: "2px",
              padding: "3px",
              background: "rgba(14,18,27,0.92)",
              backdropFilter: "blur(10px)",
              border: `1px solid ${THEME.border}`,
              borderRadius: "10px",
              zIndex: 10,
            }}
          >
            {(["2d", "3d", "text"] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDim(d)}
                title={
                  d === "3d"
                    ? "3D view (POC)"
                    : d === "text"
                    ? "Text view (outline / DOT / JSON)"
                    : "2D view"
                }
                style={{
                  background: dim === d ? THEME.accent : "transparent",
                  color: dim === d ? "#0b0e15" : "#94a3b8",
                  border: "none",
                  borderRadius: "7px",
                  cursor: "pointer",
                  fontSize: "11px",
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  padding: "4px 12px",
                }}
              >
                {d.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Floating Controls overlay (same chrome as side panels) */}
          <div
            className="anim-controls"
            style={{
              position: "absolute",
              top: "12px",
              left: "12px",
              width: "210px",
              background: "rgba(14,18,27,0.92)",
              backdropFilter: "blur(10px)",
              border: `1px solid ${THEME.border}`,
              borderRadius: "12px",
              zIndex: 10,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "12px 14px 10px",
                borderBottom: `1px solid ${THEME.border}`,
              }}
            >
              <SectionHeader
                label="Controls"
                trailing={
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    {controlsOpen && (
                      <span
                        onClick={handleReset}
                        className="link-reset"
                        style={{
                          fontSize: "11px",
                          color: THEME.accent,
                          cursor: "pointer",
                        }}
                      >
                        Reset
                      </span>
                    )}
                    <button
                      onClick={() => setControlsOpen((o) => !o)}
                      title={controlsOpen ? "Collapse" : "Expand"}
                      className="collapse-btn"
                      style={collapseBtnStyle}
                    >
                      {controlsOpen ? "▾" : "▸"}
                    </button>
                  </div>
                }
              />
            </div>
            <div
              style={{
                maxHeight: controlsOpen ? "600px" : "0px",
                opacity: controlsOpen ? 1 : 0,
                overflow: "hidden",
                transition:
                  "max-height 260ms cubic-bezier(0.4,0,0.2,1), opacity 180ms ease",
              }}
            >
              <Controls
                availableLangs={availableLangs}
                filters={filters}
                onLangToggle={handleLangToggle}
                onTypeToggle={handleTypeToggle}
                onDepthChange={handleDepthChange}
                onOverlayToggle={handleOverlayToggle}
              />
            </div>
          </div>

          {/* Hint line (canvas modes only) */}
          {dim !== "text" && (
          <div
            style={{
              position: "absolute",
              bottom: "12px",
              left: "50%",
              transform: "translateX(-50%)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              color: "#475569",
              fontSize: "11px",
              fontFamily: "monospace",
              pointerEvents: "none",
              zIndex: 5,
              background: "rgba(14,18,27,0.6)",
              border: `1px solid ${THEME.border}`,
              borderRadius: "999px",
              padding: "4px 12px",
              backdropFilter: "blur(6px)",
            }}
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            >
              <circle cx="11" cy="11" r="7" />
              <circle cx="11" cy="11" r="2.5" />
            </svg>
            {dim === "3d"
              ? "Drag to orbit · scroll to zoom · click a node to inspect · right-drag to pan"
              : "Click to select · double-click to focus · drag to move · scroll to zoom"}
          </div>
          )}
        </div>

        {/* ── Right: Detail panel ── */}
        <div
          className="anim-detail-panel"
          style={{
            width: detailWidth,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            backgroundColor: THEME.bgPanel,
            border: `1px solid ${THEME.border}`,
            borderRadius: "12px",
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: "12px 14px 10px",
              flexShrink: 0,
              borderBottom: `1px solid ${THEME.border}`,
            }}
          >
            <SectionHeader label="Node Detail" />
          </div>
          <DetailPanel
            key={selectedNodeId ?? "__empty__"}
            node={selectedNode}
            allNodes={data.nodes}
            edges={data.edges}
            onSelectNeighbor={handleSelect}
            onFocus={handleFocus}
          />
        </div>
      </div>
    </div>
  );
}
