/** Wire-shaped fixtures for the panel tests: one object per shape the page reads.
 *
 * Every field is one `api/types.ts` declares and `tests/observer/schemas/*.json` serves, and
 * `wire.fixture.test.ts` walks both to prove it, so a fixture cannot drift into a shape the
 * daemon has never produced and certify a panel against it (M9).
 */
import type {
  Assessment,
  Budget,
  Decision,
  EvalStratum,
  FlowNode,
  FlowPayload,
  FlowView,
  HubMark,
  LogReport,
  RateLimit,
  RefinementPayload,
  RefinementRow,
  RefinementsReport,
  RefinementsView,
  Repo,
  RunDetailView,
  RunRow,
  RunnerEval,
  RunsView,
  Status,
  ToolCall,
  TuningRow,
  UnresolvedLeaf,
  VectorStatus,
} from "./types";

export function budget(over: Partial<Budget> = {}): Budget {
  return {
    spent_usd: 1.25,
    runs: 3,
    max_cost_usd_per_day: 5,
    max_runs_per_day: 20,
    priced: true,
    remaining_fraction: 0.75,
    low: false,
    exhausted: false,
    ...over,
  };
}

export function rateLimit(over: Partial<RateLimit> = {}): RateLimit {
  return { max_utilization: 0.2, paused: false, resumes_at: null, ...over };
}

export function repo(over: Partial<Repo> = {}): Repo {
  return {
    repo: "/w/repo",
    identity: "1f2e3d4c",
    repo_dir_key: "a".repeat(40),
    attached: true,
    sessions: 1,
    queued: false,
    state: "observing",
    budget: budget(),
    limits: rateLimit(),
    ...over,
  };
}

export function stratum(over: Partial<EvalStratum> = {}): EvalStratum {
  return {
    suite: "graph-edges",
    stratum: "calls",
    n: 40,
    precision: 0.92,
    lower_bound_95: 0.81,
    proven: true,
    ...over,
  };
}

export function runnerEval(over: Partial<RunnerEval> = {}): RunnerEval {
  return {
    runner: "claude",
    model: "claude-sonnet-4-5-20250929",
    measured: 0,
    proven: 0,
    strata: [],
    ...over,
  };
}

export function vectors(over: Partial<VectorStatus> = {}): VectorStatus {
  return { enabled: false, model: "", ready: false, ...over };
}

export function status(over: Partial<Status> = {}): Status {
  return {
    home: "/h",
    version: "0.10.5",
    compat: 1,
    state: "running",
    started_at: 1_700_000_000,
    uptime_seconds: 120,
    idle_seconds: 8,
    repos: [repo()],
    queued_repos: 0,
    drained_events: 0,
    evals: [runnerEval(), runnerEval({ runner: "codex", model: "" })],
    vectors: vectors(),
    ...over,
  };
}

export function runRow(over: Partial<RunRow> = {}): RunRow {
  return {
    run_id: "3f2a1b9c44de4c7f",
    status: "succeeded",
    producer: "observer",
    client: "cli",
    runner: "claude",
    trigger_kind: "edit",
    trigger_detail: {},
    model: "claude-sonnet-4-5-20250929",
    summary: null,
    error: null,
    session_id: "b71ce0f2aa11",
    branch: "main",
    commit_sha: "309bb81ac4419f",
    cost_usd: 0.04,
    cost_estimated: false,
    started_at: 1_700_000_000,
    finished_at: 1_700_000_042,
    ...over,
  };
}

/** The row a `cli` producer writes outside a checkout: four nullable columns, all null. */
export function cliRunRow(over: Partial<RunRow> = {}): RunRow {
  return runRow({
    run_id: "cli0000000000000",
    producer: "cli",
    runner: "none",
    trigger_kind: "manual",
    model: null,
    session_id: null,
    branch: null,
    commit_sha: null,
    ...over,
  });
}

export function logReport(over: Partial<LogReport> = {}): LogReport {
  const runs = over.runs ?? [runRow()];
  return {
    runs,
    hidden_count: 0,
    run_count: runs.length,
    truncated: false,
    ...over,
  };
}

export function runsView(over: Partial<LogReport> = {}): RunsView {
  return { log: logReport(over) };
}

export function refinementPayload(
  over: Partial<RefinementPayload> = {},
): RefinementPayload {
  return { label: null, ...over };
}

export function refinementRow(over: Partial<RefinementRow> = {}): RefinementRow {
  return {
    refinement_id: "r-1",
    run_id: "3f2a1b9c44de4c7f",
    kind: "add_edge",
    tier: "A",
    status: "active",
    src: "app/cli.py::main",
    dst: "app/engine.py::run",
    edge_kind: "calls",
    node_id: null,
    from_dst: null,
    members: [],
    payload: refinementPayload(),
    reason: "the call is dispatched through a registry",
    confidence: 0.9,
    drifted: false,
    ...over,
  };
}

export function refinementsReport(
  over: Partial<RefinementsReport> = {},
): RefinementsReport {
  const rows = over.rows ?? [refinementRow()];
  return { rows, refinement_count: rows.length, truncated: false, ...over };
}

export function refinementsView(
  over: Partial<RefinementsReport> = {},
): RefinementsView {
  return { refinements: refinementsReport(over) };
}

export function toolCall(over: Partial<ToolCall> = {}): ToolCall {
  return { tool: "read_file", ts: 1_700_000_001, detail: "app/cli.py", ...over };
}

export function tuningRow(over: Partial<TuningRow> = {}): TuningRow {
  return {
    tuning_id: 1,
    key: "stopwords",
    value: "helper",
    status: "pending",
    reason: "every module says helper",
    metrics: {
      modularity: 0.51,
      clusters: 22,
      singletons: 5,
      top_cluster_share: 0.2,
      stranded_pins: 0,
      name_edge_churn: 0.0041,
      label_churn: 0.25,
      measured_at: 1_700_000_000,
      refused: "",
      baseline: {
        modularity: 0.5,
        clusters: 24,
        singletons: 6,
        top_cluster_share: 0.21,
        stranded_pins: 0,
      },
    },
    created_at: 1_700_000_000,
    ...over,
  };
}

export function decision(over: Partial<Decision> = {}): Decision {
  return { decision: "run", reason: "two pairs changed", ...over };
}

export function assessment(over: Partial<Assessment> = {}): Assessment {
  return { verdict: decision(), ...over };
}

export function runDetail(over: Partial<RunDetailView> = {}): RunDetailView {
  return {
    run: runRow(),
    prompt: "walk the changed pairs",
    tool_trace: [toolCall()],
    refinements: [refinementRow()],
    trials: [],
    assessment: assessment(),
    ...over,
  };
}

export function unresolvedLeaf(over: Partial<UnresolvedLeaf> = {}): UnresolvedLeaf {
  return {
    name: "requests.get",
    fact_kind: "call",
    reason: "no binding in this repo",
    external: true,
    ...over,
  };
}

export function hubMark(over: Partial<HubMark> = {}): HubMark {
  return { count: 6, kind: "expansion", collapsed: true, ...over };
}

export function flowNode(over: Partial<FlowNode> = {}): FlowNode {
  return {
    id: "app/cli.py::main",
    kind: "function",
    edge: null,
    source: "resolver",
    depth: 0,
    seen_ref: false,
    cycle: false,
    stopped: false,
    hub: null,
    unresolved: [],
    children: [],
    ...over,
  };
}

export function flowPayload(over: Partial<FlowPayload> = {}): FlowPayload {
  return { root: flowNode(), direction: "out", truncated: false, ...over };
}

export function flowView(over: Partial<FlowView> = {}): FlowView {
  return { symbol: "main", flow: flowPayload(), ...over };
}
