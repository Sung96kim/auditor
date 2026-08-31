import type { PollState } from "../api/poll";
import type { Status } from "../api/types";
import { THEME, TONE } from "../theme";
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
import Panel, { block, microLabel, mono } from "./Panel";
import RunnerMark from "./RunnerMark";

const METER_TONE: Record<Meter["tone"], string> = {
  ok: TONE.busy,
  low: TONE.warn,
  spent: TONE.bad,
};

/** One labelled bar. `known` is false for a repo whose loop has not published a budget yet. */
function Bar({ title, meter }: { title: string; meter: Meter }) {
  return (
    <div style={block}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
        <span style={microLabel}>{title}</span>
        <span style={mono}>{meter.label}</span>
      </div>
      <div style={{ height: "4px", borderRadius: "999px", background: THEME.bgElevated }}>
        <div
          style={{
            height: "100%",
            width: `${Math.round(meter.fill * 100)}%`,
            borderRadius: "999px",
            background: meter.known ? METER_TONE[meter.tone] : "transparent",
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
    <Panel
      title="Observer"
      testId="chrome"
      trailing={data ? <span style={mono}>{`${data.state} · ${repoState(selected)}`}</span> : null}
    >
      {status.phase === "loading" ? <Loading what="the daemon" /> : null}
      {status.phase === "error" ? <Failed error={status.error} onRetry={onRetry} /> : null}
      {status.phase === "stale" ? (
        <Reconnecting error={status.error} onRetry={onRetry} />
      ) : null}
      {data ? (
        <>
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

          <div style={block}>
            <span style={microLabel}>Latest eval</span>
            {evalLines(data.evals).map((line) => (
              <div
                key={line.runner}
                style={{ display: "flex", alignItems: "center", gap: "6px", ...mono }}
              >
                <RunnerMark runner={line.runner} />
                <span>{line.model}</span>
                <span style={{ marginLeft: "auto" }}>
                  {line.measured
                    ? `${line.proven} proven, floor ${line.floor?.toFixed(2) ?? "n/a"}`
                    : "no eval yet"}
                </span>
              </div>
            ))}
          </div>

          <span style={mono}>{vectorLabel(data.vectors)}</span>
        </>
      ) : null}
    </Panel>
  );
}
