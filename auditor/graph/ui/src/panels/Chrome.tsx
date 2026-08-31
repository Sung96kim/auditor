import type { PollState } from "../api/poll";
import type { Status } from "../api/types";
import { TEXT, THEME, TONE, TONE_WASH } from "../theme";
import {
  budgetMeter,
  evalLines,
  limitMeter,
  repoLabel,
  repoState,
  stateTone,
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

/** A track with no reading is hatched, so "not published yet" cannot be misread as "nothing left". */
const UNKNOWN_TRACK =
  "repeating-linear-gradient(115deg, rgba(122,139,163,0.28) 0 4px, transparent 4px 8px)";

/** Spec 12.1's state badge: the selected repo's loop state, coloured by what that state means. */
function Badge({ state }: { state: string }) {
  const tone = stateTone(state);
  return (
    <span
      style={{
        background: TONE_WASH[tone],
        border: `1px solid ${TONE[tone]}55`,
        borderRadius: "999px",
        color: TONE[tone],
        fontFamily: mono.fontFamily,
        fontSize: "10.5px",
        padding: "2px 8px",
        whiteSpace: "nowrap",
      }}
    >
      {state}
    </span>
  );
}

/** One labelled bar. `known` is false for a repo whose loop has not published a budget yet. */
function Bar({ title, meter }: { title: string; meter: Meter }) {
  const pct = Math.round(meter.fill * 100);
  return (
    <div style={block}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
        <span style={microLabel}>{title}</span>
        <span style={{ ...mono, color: meter.known ? TEXT.body : TEXT.label }}>{meter.label}</span>
      </div>
      <div
        aria-label={meter.known ? title : undefined}
        aria-valuenow={meter.known ? pct : undefined}
        aria-valuemin={meter.known ? 0 : undefined}
        aria-valuemax={meter.known ? 100 : undefined}
        role={meter.known ? "progressbar" : undefined}
        style={{
          background: meter.known ? THEME.bgElevated : UNKNOWN_TRACK,
          borderRadius: "999px",
          height: "5px",
          overflow: "hidden",
        }}
      >
        {meter.known ? (
          <div
            style={{
              background: METER_TONE[meter.tone],
              borderRadius: "999px",
              height: "100%",
              transition: "width 320ms cubic-bezier(0.22, 1, 0.36, 1), background 200ms ease",
              width: pct === 0 ? "0" : `max(4px, ${pct}%)`,
            }}
          />
        ) : null}
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
      trailing={data ? <Badge state={repoState(selected)} /> : null}
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
          {selected ? <Bar title="Rate limit" meter={limitMeter(selected.limits, now)} /> : null}

          <div style={block}>
            <span style={microLabel}>Latest eval</span>
            {evalLines(data.evals).map((line) => (
              <div
                key={line.runner}
                style={{ alignItems: "center", display: "flex", gap: "7px", ...mono }}
              >
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
                  {line.measured
                    ? `${line.proven} proven, floor ${line.floor?.toFixed(2) ?? "n/a"}`
                    : "no eval yet"}
                </span>
              </div>
            ))}
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
            <span style={{ ...mono, color: TEXT.label }}>daemon {data.state}</span>
          </div>
        </>
      ) : null}
    </Panel>
  );
}
