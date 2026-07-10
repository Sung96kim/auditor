import { useRef } from "react";
import type { GNode } from "../types";
import { THEME } from "../theme";
import { onEnterOrSpace } from "../a11y";

interface ExplorerProps {
  nodes: GNode[];
  query: string;
  onQueryChange: (q: string) => void;
  onSelect: (nodeId: string) => void;
  selectedNodeId: string | null;
}

export default function Explorer({
  nodes,
  query,
  onQueryChange,
  onSelect,
  selectedNodeId,
}: ExplorerProps) {
  const q = query.trim().toLowerCase();
  const filtered = q
    ? nodes.filter(
        (n) =>
          n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q)
      )
    : nodes;

  // Key changes whenever the filtered list identity changes (node count or query),
  // causing the list container to remount and replay the fade-in animation.
  const listKey = `${filtered.length}-${q}`;
  const prevLengthRef = useRef(filtered.length);
  const listChanged = prevLengthRef.current !== filtered.length || q !== "";
  prevLengthRef.current = filtered.length;
  void listChanged; // used only for key

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        flex: 1,
      }}
    >
      {/* Search input */}
      <div style={{ padding: "8px 10px", flexShrink: 0, position: "relative" }}>
        <input
          type="text"
          aria-label="Search symbols"
          placeholder="Search symbols…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          className="search-input"
          style={{
            width: "100%",
            boxSizing: "border-box",
            background: THEME.bgElevated,
            border: `1px solid ${THEME.border}`,
            borderRadius: "6px",
            color: "#c8d3e0",
            fontSize: "12px",
            fontFamily: "monospace",
            padding: "6px 26px 6px 9px",
            outline: "none",
          }}
        />
        {query && (
          <button
            onClick={() => onQueryChange("")}
            title="Clear"
            aria-label="Clear search"
            className="search-clear"
            style={{
              position: "absolute",
              top: "50%",
              right: "18px",
              transform: "translateY(-50%)",
              background: "transparent",
              border: "none",
              color: "#64748b",
              cursor: "pointer",
              fontSize: "15px",
              lineHeight: 1,
              padding: "2px 4px",
            }}
          >
            ×
          </button>
        )}
      </div>

      {/* Results list */}
      <div
        key={listKey}
        className="anim-list"
        style={{
          flex: 1,
          overflowY: "auto",
          minHeight: 0,
        }}
      >
        {filtered.length === 0 ? (
          <div
            style={{
              padding: "24px 16px",
              textAlign: "center",
              color: "#4B5563",
              fontSize: "12px",
            }}
          >
            No matching nodes
          </div>
        ) : (
          filtered.map((n) => {
            const isSelected = selectedNodeId === n.id;
            return (
              <div
                key={n.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(n.id)}
                onKeyDown={onEnterOrSpace(() => onSelect(n.id))}
                className="explorer-row"
                style={{
                  padding: "6px 12px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  background: isSelected ? THEME.bgElevated : "transparent",
                  borderLeft: isSelected
                    ? `2px solid ${THEME.accent}`
                    : "2px solid transparent",
                }}
              >
                <span
                  style={{
                    fontSize: "10px",
                    color: "#64748b",
                    flexShrink: 0,
                    minWidth: "44px",
                  }}
                >
                  {n.type}
                </span>
                <span
                  style={{
                    fontSize: "12px",
                    color: "#c8d3e0",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {n.label}
                </span>
                {n.findings.length > 0 && (
                  <span
                    style={{
                      fontSize: "10px",
                      background: "#7C2020",
                      color: "#FCA5A5",
                      borderRadius: "4px",
                      padding: "1px 5px",
                      flexShrink: 0,
                    }}
                  >
                    {n.findings.length}
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
