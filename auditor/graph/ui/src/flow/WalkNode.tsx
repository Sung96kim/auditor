import { TEXT, THEME } from "../theme";
import type { Placed } from "./tree";

export const NODE_W = 180;
export const NODE_H = 28;

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

export interface WalkNodeProps {
  row: Placed;
  at: Origin;
  onToggle: (key: string) => void;
}

/** Only a hub is a control: the rest of the walk is read, not pressed.
 *
 * The full id is the accessible name on both, because the label is truncated to fit the box and
 * a `title` is a tooltip rather than a name.
 */
export default function WalkNode({ row, at, onToggle }: WalkNodeProps) {
  const label = row.id.split("::").pop();
  if (row.hub === null) {
    return (
      <div
        role="listitem"
        aria-label={row.id}
        style={{ ...placement(row, at), lineHeight: `${NODE_H}px` }}
        title={row.id}
      >
        {label}
      </div>
    );
  }
  return (
    <button
      type="button"
      aria-expanded={!row.collapsed}
      aria-label={row.id}
      className="flow-node"
      onClick={() => onToggle(row.key)}
      title={row.id}
      style={{ ...placement(row, at), cursor: "pointer", display: "flex", alignItems: "center" }}
    >
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
      {row.collapsed ? (
        <span style={{ color: THEME.accent, flexShrink: 0, marginLeft: "auto" }}>+{row.hub}</span>
      ) : null}
    </button>
  );
}
