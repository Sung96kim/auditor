import type { PollState } from "../api/poll";
import type { Status } from "../api/types";
import { THEME } from "../theme";
import {
  budgetMeter,
  evalLines,
  limitMeter,
  repoLabel,
  repoState,
  vectorLabel,
  type Meter,
} from "./chrome";
import { Failed, Loading, Reconnecting } from "./States";

const TONE: Record<Meter["tone"], string> = {
  ok: THEME.accent,
  low: "#f59e0b",
  spent: "#ef4444",
};

const card: React.CSSProperties = {
  padding: "12px 14px",
  borderRadius: "10px",
  border: `1px solid ${THEME.border}`,
  backgroundColor: THEME.bgPanel,
  display: "flex",
  flexDirection: "column",
  gap: "10px",
};

const label: React.CSSProperties = {
  fontSize: "10.5px",
  fontWeight: 700,
  letterSpacing: "0.09em",
  color: "#64748b",
  textTransform: "uppercase",
};

/** One labelled bar. `known` is false for a repo whose loop has not published a budget yet. */
function Bar({ title, meter }: { title: string; meter: Meter }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
        <span style={label}>{title}</span>
        <span style={{ fontSize: "11px", color: "#94a3b8", fontFamily: "monospace" }}>
          {meter.label}
        </span>
      </div>
      <div style={{ height: "4px", borderRadius: "999px", background: THEME.bgElevated }}>
        <div
          style={{
            height: "100%",
            width: `${Math.round(meter.fill * 100)}%`,
            borderRadius: "999px",
            background: meter.known ? TONE[meter.tone] : "transparent",
          }}
        />
      </div>
    </div>
  );
}

export interface ChromeProps {
  status: PollState<Status>;
  repo: string;
  onChooseRepo: (repo: string) => void;
  onRetry: () => void;
}

/** Spec 12.1's C5 to C10: the switcher, the badge, the two meters, the evals and the vectors. */
export default function Chrome({ status, repo, onChooseRepo, onRetry }: ChromeProps) {
  const data = status.data;
  const selected = data?.repos.find((r) => r.repo === repo) ?? null;
  const now = Date.now() / 1000;
  return (
    <div style={card} data-testid="chrome">
      {status.phase === "loading" ? <Loading what="the daemon" /> : null}
      {status.phase === "error" ? <Failed error={status.error} onRetry={onRetry} /> : null}
      {status.phase === "stale" ? (
        <Reconnecting error={status.error} onRetry={onRetry} />
      ) : null}
      {data ? (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
            <span style={label}>Observer</span>
            <span style={{ fontSize: "11px", color: "#94a3b8", fontFamily: "monospace" }}>
              {data.state} - {repoState(selected)}
            </span>
          </div>

          <select
            aria-label="Repository"
            value={repo}
            onChange={(e) => onChooseRepo(e.target.value)}
            style={{
              background: THEME.bgElevated,
              color: "#e2e8f0",
              border: `1px solid ${THEME.border}`,
              borderRadius: "7px",
              padding: "5px 8px",
              fontSize: "12px",
            }}
          >
            <option value="">Choose a repo</option>
            {data.repos.map((r) => (
              <option key={r.repo} value={r.repo}>
                {repoLabel(r)}
              </option>
            ))}
          </select>

          <Bar title="Budget" meter={budgetMeter(selected?.budget ?? null)} />
          {selected ? <Bar title="Rate limit" meter={limitMeter(selected.limits, now)} /> : null}

          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <span style={label}>Latest eval</span>
            {evalLines(data.evals).map((line) => (
              <div
                key={line.runner}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontSize: "11.5px",
                  color: "#94a3b8",
                }}
              >
                <span style={{ color: "#cbd5e1" }}>{line.runner}</span>
                <span style={{ fontFamily: "monospace" }}>{line.model}</span>
                <span style={{ marginLeft: "auto", fontFamily: "monospace" }}>
                  {line.measured
                    ? `${line.proven} proven, floor ${line.floor?.toFixed(2) ?? "n/a"}`
                    : "no eval yet"}
                </span>
              </div>
            ))}
          </div>

          <span style={{ fontSize: "11px", color: "#64748b" }}>{vectorLabel(data.vectors)}</span>
        </>
      ) : null}
    </div>
  );
}
