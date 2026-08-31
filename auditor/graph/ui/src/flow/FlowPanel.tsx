import { useEffect, useMemo, useState } from "react";
import { getJson } from "../api/client";
import { failed, initial, received, type PollState } from "../api/poll";
import type { FlowView } from "../api/types";
import { TEXT, THEME } from "../theme";
import Panel from "../panels/Panel";
import { Empty, Failed, Loading } from "../panels/States";
import { flatten, layered } from "./tree";

const NODE_W = 180;
const NODE_H = 28;

/** Spec 12.1's C16 to C20: the toggle, the slider, the layered layout and the collapsed hubs. */
export default function FlowPanel({ base, repo }: { base: string; repo: string }) {
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState<"out" | "in">("out");
  const [depth, setDepth] = useState(4);
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  const [state, setState] = useState<PollState<FlowView>>(() => initial<FlowView>(null));

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    const query = new URLSearchParams({
      repo,
      symbol,
      direction,
      depth: String(depth),
    });
    getJson<FlowView>(`${base}api/flow?${query}`, "")
      .then((got) => {
        if (alive) setState((prev) => received(prev, got.value));
      })
      .catch((err) => {
        if (alive) setState((prev) => failed(prev, String(err)));
      });
    return () => {
      alive = false;
    };
  }, [base, repo, symbol, direction, depth]);

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

  const width = Math.max(...rows.map((r) => r.x + NODE_W), NODE_W) + 20;
  const height = Math.max(...rows.map((r) => r.y + NODE_H), NODE_H) + 20;
  const placed = new Map(rows.map((r) => [r.key, r]));

  return (
    <Panel title="Flow" testId="FlowPanel">
      <input
        aria-label="Symbol"
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
        placeholder="symbol"
        style={{
          background: THEME.bgElevated,
          color: "#e2e8f0",
          border: `1px solid ${THEME.border}`,
          borderRadius: "7px",
          padding: "5px 8px",
          fontSize: "12px",
        }}
      />

      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        {(["out", "in"] as const).map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={direction === d}
            onClick={() => setDirection(d)}
            style={{
              background: direction === d ? THEME.accent : "transparent",
              color: direction === d ? "#0b0e15" : "#94a3b8",
              border: `1px solid ${THEME.border}`,
              borderRadius: "7px",
              cursor: "pointer",
              fontSize: "11px",
              padding: "3px 10px",
            }}
          >
            {d}
          </button>
        ))}
        <input
          aria-label="Depth"
          type="range"
          min={0}
          max={8}
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <span style={{ fontSize: "11px", color: TEXT.label, fontFamily: "monospace" }}>
          depth {depth}
        </span>
      </div>

      {!symbol ? (
        <Empty what="a flow" hint="search for a symbol to walk its callers or its callees" />
      ) : null}
      {symbol && state.phase === "loading" ? <Loading what="the flow walk" /> : null}
      {symbol && state.phase === "error" ? (
        <Failed error={state.error} onRetry={() => setState(initial<FlowView>(null))} />
      ) : null}
      {symbol && state.phase === "ready" && state.data?.flow === null ? (
        <Empty what="flow for that symbol" hint="the graph does not hold a node by that name" />
      ) : null}

      {rows.length > 0 ? (
        <div style={{ overflow: "auto", maxHeight: "320px", position: "relative" }}>
          <div style={{ position: "relative", width, height }}>
            <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
              {rows.map((row) => {
                const parent = row.parent ? placed.get(row.parent) : undefined;
                if (!parent) return null;
                return (
                  <line
                    key={`${row.key}-edge`}
                    x1={parent.x + NODE_W}
                    y1={parent.y + NODE_H / 2}
                    x2={row.x}
                    y2={row.y + NODE_H / 2}
                    stroke={THEME.border}
                    strokeWidth={1}
                  />
                );
              })}
            </svg>
            {rows.map((row) => (
              <button
                key={row.key}
                type="button"
                onClick={() => (row.hub === null ? undefined : toggle(row.key))}
                title={row.id}
                style={{
                  position: "absolute",
                  left: `${row.x}px`,
                  top: `${row.y}px`,
                  width: `${NODE_W}px`,
                  height: `${NODE_H}px`,
                  background: THEME.bgElevated,
                  border: `1px solid ${row.unresolved && !row.external ? THEME.accent : THEME.border}`,
                  borderRadius: "6px",
                  color: "#cbd5e1",
                  cursor: row.hub === null ? "default" : "pointer",
                  fontSize: "11px",
                  opacity: row.external ? 0.55 : 1,
                  overflow: "hidden",
                  textAlign: "left",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  padding: "0 6px",
                }}
              >
                {row.id.split("::").pop()}
                {row.hub !== null && row.collapsed ? ` +${row.hub} more` : ""}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </Panel>
  );
}
