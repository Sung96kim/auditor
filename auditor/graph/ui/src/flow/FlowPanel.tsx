import { useMemo, useState } from "react";
import { useFetchOnce } from "../api/useFetchOnce";
import type { FlowView } from "../api/types";
import { TEXT, THEME } from "../theme";
import Panel, { microLabel, mono } from "../panels/Panel";
import { Empty, Failed, Loading, Reconnecting } from "../panels/States";
import { flatten, layered, type Placed } from "./tree";

const NODE_W = 180;
const NODE_H = 28;

/** Bright enough to read as a connection on the panel, quiet enough to stay behind the nodes. */
const EDGE_STROKE = "rgba(122, 139, 163, 0.38)";

const node: React.CSSProperties = {
  background: THEME.bgElevated,
  borderRadius: "6px",
  color: TEXT.strong,
  fontSize: "11px",
  height: `${NODE_H}px`,
  overflow: "hidden",
  padding: "0 7px",
  position: "absolute",
  textAlign: "left",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  width: `${NODE_W}px`,
};

export interface Origin {
  x: number;
  y: number;
}

/** dagre lays the walk out around its own centre, which leaves the panel a margin of nothing
 * above and to the left of the root. The drawing is shifted back onto its own corner. */
export function origin(rows: Placed[]): Origin {
  if (rows.length === 0) return { x: 0, y: 0 };
  return {
    x: Math.min(...rows.map((r) => r.x)),
    y: Math.min(...rows.map((r) => r.y)),
  };
}

/** Spec 12.1: an unresolved leaf is highlighted, and an externally bound one is dimmed instead. */
function placement(row: Placed, at: Origin): React.CSSProperties {
  return {
    ...node,
    border: row.external
      ? `1px dashed ${THEME.border}`
      : `1px solid ${row.unresolved ? THEME.accent : THEME.border}`,
    left: `${row.x - at.x}px`,
    opacity: row.external ? 0.6 : 1,
    top: `${row.y - at.y}px`,
  };
}

/** Only a hub is a control: the rest of the walk is read, not pressed. */
function Node({
  row,
  at,
  onToggle,
}: {
  row: Placed;
  at: Origin;
  onToggle: (key: string) => void;
}) {
  const label = row.id.split("::").pop();
  if (row.hub === null) {
    return (
      <div style={{ ...placement(row, at), lineHeight: `${NODE_H}px` }} title={row.id}>
        {label}
      </div>
    );
  }
  return (
    <button
      type="button"
      aria-expanded={!row.collapsed}
      className="flow-node"
      onClick={() => onToggle(row.key)}
      title={row.id}
      style={{ ...placement(row, at), cursor: "pointer", display: "flex", alignItems: "center" }}
    >
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
      {row.collapsed ? (
        <span style={{ color: THEME.accent, flexShrink: 0, marginLeft: "auto" }}>
          +{row.hub}
        </span>
      ) : null}
    </button>
  );
}

/** Spec 12.1's C16 to C20: the toggle, the slider, the layered layout and the collapsed hubs. */
export default function FlowPanel({ base, repo }: { base: string; repo: string }) {
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState<"out" | "in">("out");
  const [depth, setDepth] = useState(4);
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  const query = new URLSearchParams({
    repo,
    symbol,
    direction,
    depth: String(depth),
  });
  const { state, retry } = useFetchOnce<FlowView>(
    `${base}api/flow?${query}`,
    Boolean(symbol),
  );

  const rows = useMemo(() => {
    const root = state.data?.flow?.root;
    return root ? layered(flatten(root, opened), NODE_W, NODE_H) : [];
  }, [state.data, opened]);

  const toggle = (key: string) => {
    setOpened((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const at = origin(rows);
  const width = Math.max(...rows.map((r) => r.x - at.x + NODE_W), NODE_W) + 6;
  const height = Math.max(...rows.map((r) => r.y - at.y + NODE_H), NODE_H) + 6;
  const placed = new Map(rows.map((r) => [r.key, r]));

  return (
    <Panel title="Flow" testId="FlowPanel">
      <input
        aria-label="Symbol"
        className="field"
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
        placeholder="Walk a symbol…"
        style={{
          background: THEME.bgElevated,
          color: TEXT.value,
          border: `1px solid ${THEME.border}`,
          borderRadius: "7px",
          fontSize: "12px",
          padding: "6px 8px",
          width: "100%",
        }}
      />

      <div style={{ alignItems: "center", display: "flex", gap: "9px" }}>
        <div
          style={{
            background: THEME.bgElevated,
            border: `1px solid ${THEME.border}`,
            borderRadius: "9px",
            display: "flex",
            flexShrink: 0,
            gap: "2px",
            padding: "2px",
          }}
        >
          {(["out", "in"] as const).map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={direction === d}
              className="interactive"
              onClick={() => setDirection(d)}
              style={{
                background: direction === d ? THEME.accent : "transparent",
                border: "none",
                borderRadius: "7px",
                color: direction === d ? "#0b0e15" : TEXT.body,
                cursor: "pointer",
                fontSize: "10.5px",
                fontWeight: 700,
                letterSpacing: "0.06em",
                padding: "3px 9px",
                textTransform: "uppercase",
              }}
            >
              {d}
            </button>
          ))}
        </div>
        <span style={{ ...microLabel, flexShrink: 0 }}>Depth</span>
        <input
          aria-label="Depth"
          type="range"
          min={0}
          max={8}
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
          style={{ accentColor: THEME.accent, flex: 1, minWidth: 0 }}
        />
        <span style={{ ...mono, color: TEXT.body, flexShrink: 0 }}>{depth}</span>
      </div>

      {!symbol ? (
        <Empty what="flow" hint="search for a symbol to walk its callers or its callees" />
      ) : null}
      {symbol && state.phase === "loading" ? <Loading what="the flow walk" /> : null}
      {symbol && state.phase === "error" ? (
        <Failed error={state.error} onRetry={retry} />
      ) : null}
      {symbol && state.phase === "stale" ? (
        <Reconnecting error={state.error} onRetry={retry} />
      ) : null}
      {symbol && state.phase === "ready" && state.data?.flow === null ? (
        <Empty what="flow for that symbol" hint="the graph does not hold a node by that name" />
      ) : null}

      {rows.length > 0 ? (
        <div
          style={{
            margin: "0 -14px",
            maxHeight: "320px",
            overflow: "auto",
            padding: "0 14px",
            position: "relative",
          }}
        >
          <div style={{ position: "relative", width, height }}>
            <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
              {rows.map((row) => {
                const parent = row.parent ? placed.get(row.parent) : undefined;
                if (!parent) return null;
                return (
                  <line
                    key={`${row.key}-edge`}
                    x1={parent.x - at.x + NODE_W}
                    y1={parent.y - at.y + NODE_H / 2}
                    x2={row.x - at.x}
                    y2={row.y - at.y + NODE_H / 2}
                    stroke={EDGE_STROKE}
                    strokeWidth={1}
                  />
                );
              })}
            </svg>
            {rows.map((row) => (
              <Node key={row.key} row={row} at={at} onToggle={toggle} />
            ))}
          </div>
        </div>
      ) : null}
    </Panel>
  );
}
