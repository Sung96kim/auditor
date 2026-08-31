/** Every wire shape the page reads, hand written and pinned to the committed schemas.
 *
 * `tests/observer/test_ui_contract.py` walks this file against `tests/observer/schemas/*.json`,
 * so a server-side rename is a failing test rather than a blank panel nobody noticed (P12).
 */

export interface EvalStratum {
  suite: string;
  stratum: string;
  n: number;
  precision: number;
  lower_bound_95: number;
  proven: boolean;
}

export interface RunnerEval {
  runner: string;
  model: string;
  measured: number;
  proven: number;
  strata: EvalStratum[];
}

export interface Budget {
  spent_usd: number;
  runs: number;
  max_cost_usd_per_day: number;
  max_runs_per_day: number;
  remaining_fraction: number;
  low: boolean;
  exhausted: boolean;
}

export interface RateLimit {
  max_utilization: number;
  paused: boolean;
  resumes_at: number | null;
}

export interface Repo {
  repo: string;
  identity: string;
  repo_dir_key: string;
  attached: boolean;
  sessions: number;
  queued: boolean;
  state: string;
  budget: Budget | null;
  limits: RateLimit;
}

export interface VectorStatus {
  enabled: boolean;
  model: string;
  ready: boolean;
}

export interface Status {
  home: string;
  version: string;
  compat: number;
  state: string;
  started_at: number;
  uptime_seconds: number;
  idle_seconds: number;
  repos: Repo[];
  queued_repos: string[];
  drained_events: number;
  evals: RunnerEval[];
  vectors: VectorStatus;
}

export interface RunRow {
  run_id: string;
  status: string;
  producer: string;
  client: string;
  runner: string;
  trigger_kind: string;
  /** the one deliberately open object on the wire: its shape is the trigger's own. */
  trigger_detail: Record<string, unknown> | null;
  model: string;
  summary: string | null;
  error: string | null;
  session_id: string;
  branch: string;
  commit_sha: string;
  cost_usd: number;
  cost_estimated: boolean;
  started_at: number;
  finished_at: number | null;
}

export interface RefinementRow {
  refinement_id: string;
  run_id: string;
  kind: string;
  tier: string;
  status: string;
  src: string | null;
  dst: string | null;
  edge_kind: string | null;
  node_id: string | null;
  from_dst: string | null;
  reason: string;
  confidence: number;
  drifted: boolean;
}

export interface ToolCall {
  tool: string;
  ts: number;
  detail: string;
}

export interface TuningRow {
  tuning_id: string;
  key: string;
  status: string;
  created_at: number;
}

export interface Decision {
  decision: string;
  reason: string;
}

export interface Assessment {
  verdict: Decision | null;
}

export interface RunDetailView {
  run: RunRow | null;
  prompt: string;
  tool_trace: ToolCall[];
  refinements: RefinementRow[];
  trials: TuningRow[];
  assessment: Assessment | null;
}

/** A collapsed hub's fan. `count` is what the wire serves; there is no `fan_in` and no `shown`. */
export interface HubMark {
  count: number;
  kind: string;
  collapsed: boolean;
}

export interface UnresolvedLeaf {
  name: string;
  fact_kind: string;
  reason: string;
  /** the wire name for `externally_bound`: a third-party call rather than a genuine gap. */
  external: boolean;
}

export interface FlowNode {
  id: string;
  kind: string;
  edge: string | null;
  source: string;
  depth: number;
  seen_ref: boolean;
  cycle: boolean;
  stopped: boolean;
  hub: HubMark | null;
  unresolved: UnresolvedLeaf[];
  children: FlowNode[];
}

export interface FlowPayload {
  root: FlowNode;
  direction: string;
  truncated: boolean;
}

export interface FlowView {
  symbol: string;
  flow: FlowPayload | null;
}
