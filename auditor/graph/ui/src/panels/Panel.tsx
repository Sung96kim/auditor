import { TEXT, THEME } from "../theme";

/** The uppercase micro-label every panel heading and every field label is drawn with. */
export const microLabel: React.CSSProperties = {
  fontSize: "10.5px",
  fontWeight: 700,
  letterSpacing: "0.09em",
  color: TEXT.label,
  textTransform: "uppercase",
};

/** Numbers, ids and paths, which are read by shape rather than by word. */
export const mono: React.CSSProperties = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: "11px",
  color: TEXT.body,
};

/** A box nested inside a panel body: one step in on the radius, one step up on the surface. */
export const nested: React.CSSProperties = {
  borderRadius: "8px",
  border: `1px solid ${THEME.border}`,
  backgroundColor: THEME.bgElevated,
};

/** A labelled block inside a panel body. The gap is the column's rhythm, halved. */
export const block: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "5px",
  minWidth: 0,
};

const card: React.CSSProperties = {
  backgroundColor: THEME.bgPanel,
  border: `1px solid ${THEME.border}`,
  borderRadius: "12px",
  display: "flex",
  flexDirection: "column",
  flexShrink: 0,
  overflow: "hidden",
};

const strip: React.CSSProperties = {
  alignItems: "center",
  borderBottom: `1px solid ${THEME.border}`,
  display: "flex",
  gap: "8px",
  justifyContent: "space-between",
  padding: "12px 14px 10px",
};

const body: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "10px",
  minWidth: 0,
  padding: "12px 14px 13px",
};

/** One labelled section inside a panel body, ruled off from the one above it.
 *
 * In the shared chrome rather than in `RunDetail`, because six stacked headings with a 3px gap
 * is the failure it exists to prevent and the next panel to grow sections will want the same.
 */
export function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ ...block, borderTop: `1px solid ${THEME.border}`, paddingTop: "9px" }}>
      <span style={microLabel}>{title}</span>
      {children}
    </div>
  );
}

export interface PanelProps {
  title: string;
  /** The right-hand side of the header strip: a count, a badge, a control. */
  trailing?: React.ReactNode;
  testId: string;
  children: React.ReactNode;
}

/** The live column's card, built to the same header-strip chrome as the app's other panels.
 *
 * Every S10 surface goes through this, so the radius, the strip and the body rhythm are
 * decided once rather than copied into four files that then drift apart.
 */
export default function Panel({ title, trailing, testId, children }: PanelProps) {
  return (
    <section style={card} data-testid={testId}>
      <div style={strip}>
        <span style={microLabel}>{title}</span>
        {trailing}
      </div>
      <div style={body}>{children}</div>
    </section>
  );
}
