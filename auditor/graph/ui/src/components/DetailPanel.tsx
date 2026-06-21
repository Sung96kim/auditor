import type { GNode, GEdge } from "../types";
import { THEME, NODE_COLOR } from "../theme";

interface DetailPanelProps {
  node: GNode | null;
  allNodes: GNode[];
  edges: GEdge[];
  onSelectNeighbor: (nodeId: string) => void;
  onFocus: (nodeId: string) => void;
}

export default function DetailPanel({
  node,
  allNodes,
  edges,
  onSelectNeighbor,
  onFocus,
}: DetailPanelProps) {
  if (!node) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: "12px",
          padding: "24px",
          textAlign: "center",
        }}
      >
        <div
          style={{
            width: "52px",
            height: "52px",
            borderRadius: "14px",
            background: THEME.bgElevated,
            border: `1px solid ${THEME.border}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#4B5563"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="6" cy="6" r="2.4" />
            <circle cx="18" cy="7" r="2.4" />
            <circle cx="9" cy="18" r="2.4" />
            <path d="M7.8 7.4 16 6.6M7.2 8 8.4 15.8" />
          </svg>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          <span
            style={{
              fontSize: "13.5px",
              fontWeight: 600,
              color: "#94a3b8",
            }}
          >
            No node selected
          </span>
          <span
            style={{
              fontSize: "12px",
              color: "#4B5563",
              lineHeight: 1.5,
              maxWidth: "200px",
            }}
          >
            Click any node in the graph or explorer to inspect its dependencies,
            members, and metrics.
          </span>
        </div>
      </div>
    );
  }

  const nodeMap = new Map(allNodes.map((n) => [n.id, n]));

  const neighborIds = new Set<string>();
  for (const e of edges) {
    if (e.source === node.id) neighborIds.add(e.target);
    else if (e.target === node.id) neighborIds.add(e.source);
  }
  const neighbors = [...neighborIds]
    .map((id) => nodeMap.get(id))
    .filter((n): n is GNode => n !== undefined);

  const vscodePath = node.path
    ? `vscode://file/${node.path}:${node.line}`
    : null;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 0,
        overflowY: "auto",
        flex: 1,
      }}
    >
      {/* Node header */}
      <div style={{ padding: "14px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            marginBottom: "10px",
          }}
        >
          <span
            style={{
              width: "10px",
              height: "10px",
              borderRadius: node.type === "class" || node.type === "module" ? "50%" : "3px",
              background: NODE_COLOR[node.type] ?? THEME.accent,
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontSize: "13px",
              fontWeight: 600,
              color: "#e2e8f0",
              wordBreak: "break-all",
              flex: 1,
            }}
          >
            {node.label}
          </span>
        </div>

        {/* Qualname */}
        <div
          style={{
            fontSize: "10px",
            color: "#4B5563",
            marginBottom: "10px",
            wordBreak: "break-all",
            fontFamily: "monospace",
          }}
        >
          {node.id}
        </div>

        {/* Focus button */}
        <button
          onClick={() => onFocus(node.id)}
          style={{
            display: "block",
            width: "100%",
            marginBottom: "10px",
            padding: "5px 10px",
            background: THEME.accent,
            border: "none",
            borderRadius: "6px",
            color: "#fff",
            fontSize: "12px",
            fontFamily: "monospace",
            fontWeight: 600,
            cursor: "pointer",
            letterSpacing: "0.03em",
          }}
        >
          Focus ego graph
        </button>

        {/* Meta rows */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "5px",
            fontSize: "12px",
          }}
        >
          <div style={{ display: "flex", gap: "8px" }}>
            <span style={{ color: "#4B5563", minWidth: "48px" }}>type</span>
            <span style={{ color: "#94a3b8" }}>{node.type}</span>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <span style={{ color: "#4B5563", minWidth: "48px" }}>role</span>
            <span style={{ color: "#94a3b8" }}>{node.role}</span>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <span style={{ color: "#4B5563", minWidth: "48px" }}>lang</span>
            <span style={{ color: "#94a3b8" }}>{node.lang}</span>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <span style={{ color: "#4B5563", minWidth: "48px" }}>module</span>
            <span
              style={{
                color: "#94a3b8",
                fontFamily: "monospace",
                fontSize: "11px",
                wordBreak: "break-all",
              }}
            >
              {node.module}
            </span>
          </div>
          {vscodePath && (
            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              <span style={{ color: "#4B5563", minWidth: "48px" }}>path</span>
              <a
                href={vscodePath}
                style={{
                  color: THEME.accent,
                  fontSize: "11px",
                  fontFamily: "monospace",
                  textDecoration: "none",
                  wordBreak: "break-all",
                }}
              >
                {node.path}:{node.line}
              </a>
            </div>
          )}
        </div>
      </div>

      {/* Findings badges */}
      {node.findings.length > 0 && (
        <>
          <div
            style={{
              height: "1px",
              background: THEME.border,
              margin: "0 14px",
            }}
          />
          <div style={{ padding: "10px 14px" }}>
            <div
              style={{
                fontSize: "10px",
                fontWeight: 600,
                letterSpacing: ".7px",
                color: "#64748b",
                marginBottom: "8px",
              }}
            >
              FINDINGS
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
              {node.findings.map((f) => (
                <span
                  key={f}
                  style={{
                    fontSize: "10px",
                    background: "#7C2020",
                    color: "#FCA5A5",
                    borderRadius: "4px",
                    padding: "2px 6px",
                    fontFamily: "monospace",
                  }}
                >
                  {f}
                </span>
              ))}
            </div>
          </div>
        </>
      )}

      {/* Neighbors */}
      {neighbors.length > 0 && (
        <>
          <div
            style={{
              height: "1px",
              background: THEME.border,
              margin: "0 14px",
            }}
          />
          <div style={{ padding: "10px 14px" }}>
            <div
              style={{
                fontSize: "10px",
                fontWeight: 600,
                letterSpacing: ".7px",
                color: "#64748b",
                marginBottom: "8px",
              }}
            >
              NEIGHBORS ({neighbors.length})
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              {neighbors.map((nb) => (
                <div
                  key={nb.id}
                  onClick={() => onSelectNeighbor(nb.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "7px",
                    padding: "5px 7px",
                    borderRadius: "6px",
                    cursor: "pointer",
                    background: "transparent",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLDivElement).style.background =
                      THEME.bgElevated;
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLDivElement).style.background =
                      "transparent";
                  }}
                >
                  <span
                    style={{
                      width: "7px",
                      height: "7px",
                      borderRadius:
                        nb.type === "class" || nb.type === "module"
                          ? "50%"
                          : "2px",
                      background: NODE_COLOR[nb.type] ?? THEME.accent,
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontSize: "12px",
                      color: "#94a3b8",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {nb.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
