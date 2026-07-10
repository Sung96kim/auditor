import type { NodeType } from "../types";
import { THEME, NODE_COLOR } from "../theme";
import { onEnterOrSpace } from "../a11y";

export interface FilterState {
  langs: Set<string>;
  types: Set<NodeType>;
  depth: number;
  overlayOn: boolean;
}

interface ControlsProps {
  availableLangs: string[];
  filters: FilterState;
  onLangToggle: (lang: string) => void;
  onTypeToggle: (type: NodeType) => void;
  onDepthChange: (depth: number) => void;
  onOverlayToggle: () => void;
}

const SECTION_HEADER: React.CSSProperties = {
  fontSize: "10.5px",
  fontWeight: 700,
  letterSpacing: "0.09em",
  color: "#64748b",
  textTransform: "uppercase",
};

const ALL_TYPES: NodeType[] = ["class", "function", "method", "module"];

const TYPE_RADIUS: Record<NodeType, string> = {
  class: "50%",
  function: "3px",
  method: "3px",
  module: "50%",
};

export default function Controls({
  availableLangs,
  filters,
  onLangToggle,
  onTypeToggle,
  onDepthChange,
  onOverlayToggle,
}: ControlsProps) {
  const depthLabel =
    filters.depth === 1 ? "direct" : `${filters.depth} hops`;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 0,
        overflowY: "auto",
      }}
    >
      {/* FILTER BY LANGUAGE */}
      <section style={{ padding: "12px 14px 10px" }}>
        <div style={{ ...SECTION_HEADER, marginBottom: "10px" }}>
          Filter by Language
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          {availableLangs.map((lang) => {
            const checked = filters.langs.has(lang);
            return (
              <div
                key={lang}
                role="button"
                tabIndex={0}
                onClick={() => onLangToggle(lang)}
                onKeyDown={onEnterOrSpace(() => onLangToggle(lang))}
                className="check-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "9px",
                  padding: "6px 8px",
                  cursor: "pointer",
                  background: checked ? THEME.bgElevated : "transparent",
                }}
              >
                <span
                  style={{
                    width: "14px",
                    height: "14px",
                    borderRadius: "4px",
                    border: `1.5px solid ${checked ? THEME.accent : "#334155"}`,
                    background: checked ? THEME.accent : "transparent",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "#fff",
                    fontSize: "10px",
                    flexShrink: 0,
                    transition: "background 140ms ease, border-color 140ms ease",
                  }}
                >
                  {checked ? "✓" : ""}
                </span>
                <span style={{ flex: 1, fontSize: "13px", color: "#c8d3e0" }}>
                  {lang}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <div
        style={{ height: "1px", background: THEME.border, margin: "0 14px" }}
      />

      {/* DEPENDENCY DEPTH */}
      <section style={{ padding: "12px 14px 10px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "8px",
          }}
        >
          <span style={SECTION_HEADER}>Dependency Depth</span>
          <span
            style={{
              fontSize: "11px",
              color: "#c8d3e0",
              fontFamily: "monospace",
            }}
          >
            {depthLabel}
          </span>
        </div>
        <input
          type="range"
          aria-label="Dependency depth"
          min={1}
          max={5}
          value={filters.depth}
          onChange={(e) => onDepthChange(Number(e.target.value))}
          style={{ width: "100%", accentColor: THEME.accent }}
        />
        <div
          style={{
            fontSize: "11px",
            color: "#4B5563",
            marginTop: "7px",
            lineHeight: 1.4,
          }}
        >
          Select a node, then limit the graph to its N-hop neighborhood.
        </div>
      </section>

      <div
        style={{ height: "1px", background: THEME.border, margin: "0 14px" }}
      />

      {/* NODE TYPES */}
      <section style={{ padding: "12px 14px 10px" }}>
        <div style={{ ...SECTION_HEADER, marginBottom: "9px" }}>Node Types</div>
        <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
          {ALL_TYPES.map((t) => {
            const active = filters.types.has(t);
            return (
              <div
                key={t}
                role="button"
                tabIndex={0}
                onClick={() => onTypeToggle(t)}
                onKeyDown={onEnterOrSpace(() => onTypeToggle(t))}
                className="type-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "9px",
                  cursor: "pointer",
                  opacity: active ? 1 : 0.4,
                }}
              >
                <span
                  style={{
                    width: "9px",
                    height: "9px",
                    borderRadius: TYPE_RADIUS[t],
                    background: NODE_COLOR[t] ?? THEME.accent,
                    flexShrink: 0,
                  }}
                />
                <span style={{ flex: 1, fontSize: "12px", color: "#94a3b8" }}>
                  {t}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <div
        style={{ height: "1px", background: THEME.border, margin: "0 14px" }}
      />

      {/* FINDINGS OVERLAY */}
      <section style={{ padding: "12px 14px 10px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span style={SECTION_HEADER}>Overlay</span>
          <div
            role="switch"
            aria-checked={filters.overlayOn}
            aria-label="Findings overlay"
            tabIndex={0}
            onClick={onOverlayToggle}
            onKeyDown={onEnterOrSpace(onOverlayToggle)}
            className="overlay-track"
            style={{
              width: "36px",
              height: "20px",
              borderRadius: "10px",
              background: filters.overlayOn ? THEME.accent : "#334155",
              position: "relative",
              cursor: "pointer",
            }}
          >
            <div
              className="overlay-knob"
              style={{
                position: "absolute",
                top: "2px",
                left: filters.overlayOn ? "18px" : "2px",
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                background: "#fff",
              }}
            />
          </div>
        </div>
        <div
          style={{
            fontSize: "11px",
            color: "#4B5563",
            marginTop: "6px",
            lineHeight: 1.4,
          }}
        >
          Highlight nodes with findings badges.
        </div>
      </section>
    </div>
  );
}
