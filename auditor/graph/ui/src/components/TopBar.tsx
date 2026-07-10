import { Fragment } from "react";
import { THEME } from "../theme";
import type { Crumb, CrumbTarget } from "../graph/breadcrumb";
import { onEnterOrSpace } from "../a11y";

interface TopBarProps {
  title: string;
  crumbs: Crumb[];
  nodeCount: number;
  edgeCount: number;
  clusterCount: number;
  onCrumb: (target: CrumbTarget) => void;
}

export default function TopBar({
  title,
  crumbs,
  nodeCount,
  edgeCount,
  clusterCount,
  onCrumb,
}: TopBarProps) {
  return (
    <header
      className="anim-topbar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "18px",
        padding: "0 16px",
        height: "44px",
        flexShrink: 0,
        background: THEME.bgPanel,
        borderBottom: `1px solid ${THEME.border}`,
      }}
    >
      {/* App / repo name */}
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "8px",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: "13px",
            fontWeight: 700,
            color: THEME.accent,
            letterSpacing: "0.04em",
          }}
        >
          Auditor
        </span>
        <span
          style={{
            fontSize: "12.5px",
            color: "#c8d3e0",
            fontFamily: "monospace",
          }}
        >
          {title}
        </span>
      </div>

      {/* Breadcrumb */}
      <nav
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        {crumbs.map((crumb, i) => {
          const isLast = i === crumbs.length - 1;
          return (
            <Fragment key={`${crumb.label}-${i}`}>
              {i > 0 && (
                <span style={{ color: "#374151", fontSize: "12px", flexShrink: 0 }}>
                  ›
                </span>
              )}
              <span
                role={isLast ? undefined : "button"}
                tabIndex={isLast ? undefined : 0}
                onClick={isLast ? undefined : () => onCrumb(crumb.target)}
                onKeyDown={
                  isLast ? undefined : onEnterOrSpace(() => onCrumb(crumb.target))
                }
                className={isLast ? undefined : "crumb-link"}
                style={{
                  fontSize: "12px",
                  fontFamily: "monospace",
                  color: isLast ? THEME.accent : "#94a3b8",
                  fontWeight: isLast ? 600 : 400,
                  cursor: isLast ? "default" : "pointer",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  maxWidth: isLast ? "260px" : "200px",
                }}
              >
                {crumb.label}
              </span>
            </Fragment>
          );
        })}
      </nav>

      {/* Counts */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          flexShrink: 0,
          fontSize: "11.5px",
          fontFamily: "monospace",
          color: "#64748b",
        }}
      >
        <span>
          <strong style={{ color: "#94a3b8", fontWeight: 600 }}>{nodeCount}</strong>{" "}
          nodes
        </span>
        <span style={{ color: "#2A3240" }}>·</span>
        <span>
          <strong style={{ color: "#94a3b8", fontWeight: 600 }}>{edgeCount}</strong>{" "}
          edges
        </span>
        <span style={{ color: "#2A3240" }}>·</span>
        <span>
          <strong style={{ color: "#94a3b8", fontWeight: 600 }}>
            {clusterCount}
          </strong>{" "}
          clusters
        </span>
      </div>
    </header>
  );
}
