import { useCallback, useMemo, useState } from "react";
import type { GraphPayload, NodeType } from "./types";
import { THEME } from "./theme";
import { sample } from "./sample";
import GraphCanvas from "./components/GraphCanvas";
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

  const handleSelect = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
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
            width: sidebarWidth,
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
            <SectionHeader
              label="Explorer"
              trailing={
                <span
                  style={{
                    fontSize: "11px",
                    color: "#64748b",
                    fontFamily: "monospace",
                  }}
                >
                  {filteredPayload.nodes.length} nodes
                </span>
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
          <GraphCanvas
            payload={filteredPayload}
            view={view}
            onSelect={handleSelect}
            onDrill={handleDrill}
            onFocus={handleFocus}
            selectedNodeId={selectedNodeId}
            overlayOn={filters.overlayOn}
          />

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
                }
              />
            </div>
            <Controls
              availableLangs={availableLangs}
              filters={filters}
              onLangToggle={handleLangToggle}
              onTypeToggle={handleTypeToggle}
              onDepthChange={handleDepthChange}
              onOverlayToggle={handleOverlayToggle}
            />
          </div>

          {/* Hint line */}
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
            Click to select · double-click to focus · drag to move · scroll to zoom
          </div>
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
