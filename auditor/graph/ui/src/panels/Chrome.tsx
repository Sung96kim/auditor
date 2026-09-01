import type { PollState } from "../api/poll";
import type { EvalsView, Status } from "../api/types";
import { useFetchOnce } from "../api/useFetchOnce";
import { TEXT, THEME, TONE } from "../theme";
import {
  budgetMeter,
  evalLines,
  limitMeter,
  repoLabel,
  repoState,
  selectedRepo,
  sinceLabel,
  vectorLabel,
  type EvalLine,
} from "./chrome";
import { Failed, Loading, Reconnecting } from "./States";
import Badge from "./Badge";
import Bar from "./Bar";
import Panel, { block, microLabel, mono } from "./Panel";
import RunnerMark from "./RunnerMark";

/** One runner's latest eval: the model it ran, how many strata are proven, and each one's floor. */
function EvalRow({ line }: { line: EvalLine }) {
  return (
    <div style={{ ...block, gap: "3px" }}>
      <div style={{ alignItems: "center", display: "flex", gap: "7px", ...mono }}>
        <RunnerMark runner={line.runner} size={13} />
        <span
          title={line.model}
          style={{
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {line.model}
        </span>
        <span
          style={{
            color: line.measured ? TEXT.body : TEXT.label,
            flexShrink: 0,
            marginLeft: "auto",
            whiteSpace: "nowrap",
          }}
        >
          {line.measured ? `${line.proven} proven` : "no eval yet"}
        </span>
      </div>
      {line.strata.map((stratum) => (
        <div
          key={stratum.key}
          style={{
            ...mono,
            color: TEXT.label,
            display: "flex",
            gap: "8px",
            justifyContent: "space-between",
            paddingLeft: "20px",
          }}
        >
          <span
            style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
            title={stratum.key}
          >
            {stratum.label}
          </span>
          <span style={{ color: stratum.proven ? TONE.ok : TEXT.label, flexShrink: 0 }}>
            {stratum.lower.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  );
}

export interface ChromeProps {
  status: PollState<Status>;
  base: string;
  repo: string;
  onChooseRepo: (repo: string) => void;
  onRetry: () => void;
}

/** Spec 12.1's C5 to C10: the switcher, the badge, the two meters, the evals and the vectors. */
export default function Chrome({ status, base, repo, onChooseRepo, onRetry }: ChromeProps) {
  const data = status.data;
  const selected = selectedRepo(data?.repos ?? [], repo);
  const now = Date.now();
  // the roster rides on the poll; the measurements are their own route and their own fetch (P3)
  const evals = useFetchOnce<EvalsView>(
    `${base}api/evals?${new URLSearchParams({ repo })}`,
    Boolean(repo),
  );
  // both clock readings were served with the body: a 304 holds the body, so carry them forward
  const held = (now - status.at) / 1000;
  return (
    <Panel
      title="Observer"
      testId="chrome"
      trailing={data ? <Badge state={repoState(selected, repo)} /> : null}
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
            className="field"
            value={repo}
            onChange={(e) => onChooseRepo(e.target.value)}
            style={{
              background: THEME.bgElevated,
              color: TEXT.value,
              border: `1px solid ${THEME.border}`,
              borderRadius: "7px",
              cursor: "pointer",
              padding: "6px 8px",
              fontSize: "12px",
              width: "100%",
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
          <Bar title="Rate limit" meter={limitMeter(selected?.limits ?? null, now / 1000)} />

          <div style={block}>
            <span style={microLabel}>Latest eval</span>
            {evals.state.phase === "error" ? (
              // no confirmed answer at all: say so, never fall back to "no eval yet"
              <Failed error={evals.state.error} onRetry={evals.retry} />
            ) : (
              <>
                {evals.state.phase === "stale" ? (
                  <Reconnecting error={evals.state.error} onRetry={evals.retry} />
                ) : null}
                {evalLines(data.evals, evals.state.data?.runners ?? []).map((line) => (
                  <EvalRow key={line.runner} line={line} />
                ))}
              </>
            )}
          </div>

          <div
            style={{
              borderTop: `1px solid ${THEME.border}`,
              display: "flex",
              gap: "8px",
              justifyContent: "space-between",
              paddingTop: "9px",
            }}
          >
            <span style={{ ...mono, color: TEXT.label }}>{vectorLabel(data.vectors)}</span>
            <span style={{ ...mono, color: TEXT.label }}>
              {data.state} up {sinceLabel(data.uptime_seconds + held)}, idle{" "}
              {sinceLabel(data.idle_seconds + held)}
            </span>
          </div>
        </>
      ) : null}
    </Panel>
  );
}
