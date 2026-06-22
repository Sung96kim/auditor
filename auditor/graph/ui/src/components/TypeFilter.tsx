import { useState } from "react";
import type { NodeType } from "../types";
import { THEME, NODE_COLOR } from "../theme";

const ALL_TYPES: NodeType[] = ["class", "function", "method", "module"];

interface TypeFilterProps {
  types: Set<NodeType>;
  onToggle: (t: NodeType) => void;
}

/** Compact sidebar dropdown to filter the graph + symbol list by node type. */
export default function TypeFilter({ types, onToggle }: TypeFilterProps) {
  const [open, setOpen] = useState(false);
  const active = ALL_TYPES.filter((t) => types.has(t));
  const label =
    active.length === ALL_TYPES.length
      ? "All types"
      : active.length === 0
        ? "No types"
        : active.join(", ");

  return (
    <div style={{ padding: "0 10px 8px", flexShrink: 0, position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%",
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: THEME.bgElevated,
          border: `1px solid ${THEME.border}`,
          borderRadius: "6px",
          color: "#c8d3e0",
          fontSize: "12px",
          padding: "6px 9px",
          cursor: "pointer",
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </span>
        <span style={{ color: "#64748b", marginLeft: "6px" }}>
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: "10px",
            right: "10px",
            zIndex: 20,
            marginTop: "4px",
            background: THEME.bgPanel,
            border: `1px solid ${THEME.border}`,
            borderRadius: "8px",
            boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
            overflow: "hidden",
          }}
        >
          {ALL_TYPES.map((t) => {
            const on = types.has(t);
            return (
              <div
                key={t}
                onClick={() => onToggle(t)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "9px",
                  padding: "7px 10px",
                  cursor: "pointer",
                  background: on ? THEME.bgElevated : "transparent",
                }}
              >
                <span
                  style={{
                    width: "13px",
                    height: "13px",
                    borderRadius: "4px",
                    border: `1.5px solid ${on ? THEME.accent : "#334155"}`,
                    background: on ? THEME.accent : "transparent",
                    color: "#fff",
                    fontSize: "9px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {on ? "✓" : ""}
                </span>
                <span
                  style={{
                    width: "8px",
                    height: "8px",
                    borderRadius: "2px",
                    background: NODE_COLOR[t] ?? THEME.accent,
                    flexShrink: 0,
                  }}
                />
                <span style={{ fontSize: "12px", color: "#c8d3e0" }}>{t}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
