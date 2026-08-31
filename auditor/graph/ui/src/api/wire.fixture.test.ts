import { describe, it, expect } from "vitest";
import flowSchema from "../../../../../tests/observer/schemas/FlowView.json";
import refinementsSchema from "../../../../../tests/observer/schemas/RefinementsView.json";
import runDetailSchema from "../../../../../tests/observer/schemas/RunDetailView.json";
import runsSchema from "../../../../../tests/observer/schemas/RunsView.json";
import statusSchema from "../../../../../tests/observer/schemas/StatusPayload.json";
import * as wire from "./wire.fixture";

interface Schema {
  title: string;
  properties?: Record<string, unknown>;
  /** an enum `$def` has no `properties`, so the value is read as an open object, not a shape. */
  $defs?: Record<string, Record<string, unknown>>;
}

/** The committed snapshots themselves, imported rather than described: no second copy to drift. */
const SCHEMAS: Record<string, Schema> = {
  FlowView: flowSchema,
  RefinementsView: refinementsSchema,
  RunDetailView: runDetailSchema,
  RunsView: runsSchema,
  StatusPayload: statusSchema,
};

function definitions(root: string): Record<string, Record<string, unknown>> {
  const schema = SCHEMAS[root];
  const found: Record<string, Record<string, unknown>> = {
    [schema.title]: schema.properties ?? {},
  };
  for (const [name, body] of Object.entries(schema.$defs ?? {})) {
    found[name] = (body.properties as Record<string, unknown>) ?? {};
  }
  return found;
}

/** Every fixture, with the committed schema and the definition inside it that has to serve it. */
const CASES: [string, object, string, string][] = [
  ["budget", wire.budget(), "StatusPayload", "BudgetPayload"],
  ["rateLimit", wire.rateLimit(), "StatusPayload", "RateLimitPayload"],
  ["repo", wire.repo(), "StatusPayload", "RepoPayload"],
  ["stratum", wire.stratum(), "StatusPayload", "EvalStratumPayload"],
  ["runnerEval", wire.runnerEval(), "StatusPayload", "RunnerEvalPayload"],
  ["vectors", wire.vectors(), "StatusPayload", "VectorStatusPayload"],
  ["status", wire.status(), "StatusPayload", "StatusPayload"],
  ["runRow", wire.runRow(), "RunsView", "RunRowPayload"],
  ["cliRunRow", wire.cliRunRow(), "RunsView", "RunRowPayload"],
  ["logReport", wire.logReport(), "RunsView", "LogReport"],
  ["refinementRow", wire.refinementRow(), "RefinementsView", "RefinementRowPayload"],
  ["refinementsReport", wire.refinementsView().refinements, "RefinementsView", "RefinementsReport"],
  ["toolCall", wire.toolCall(), "RunDetailView", "ToolCall"],
  ["tuningRow", wire.tuningRow(), "RunDetailView", "TuningRow"],
  ["decision", wire.decision(), "RunDetailView", "Decision"],
  ["assessment", wire.assessment(), "RunDetailView", "Assessment"],
  ["runDetail", wire.runDetail(), "RunDetailView", "RunDetailView"],
  ["unresolvedLeaf", wire.unresolvedLeaf(), "FlowView", "UnresolvedLeaf"],
  ["hubMark", wire.hubMark(), "FlowView", "HubMark"],
  ["flowNode", wire.flowNode(), "FlowView", "FlowNode"],
  ["flowPayload", wire.flowPayload(), "FlowView", "FlowPayload"],
  ["flowView", wire.flowView(), "FlowView", "FlowView"],
];

describe("the panel fixtures are shaped like the wire", () => {
  it("covers every fixture the module exports, so a new one cannot slip in unchecked", () => {
    const named = new Set(CASES.map(([name]) => name));
    const exported = Object.keys(wire).filter((name) => typeof wire[name as keyof typeof wire] === "function");
    expect(exported.filter((name) => !named.has(name) && name !== "runsView" && name !== "refinementsView")).toEqual([]);
    expect(CASES.length).toBeGreaterThan(15);
  });

  it.each(CASES)("%s is a %s.%s and nothing else", (_name, value, root, holder) => {
    const served = definitions(root)[holder];
    expect(served, `${holder} is not in ${root}.json`).toBeTruthy();
    for (const field of Object.keys(value)) {
      expect(Object.keys(served), `${root}.${holder} does not serve ${field}`).toContain(field);
    }
  });

  it("a cli run carries the four nullable columns as null, which is what the wire allows", () => {
    const row = wire.cliRunRow();
    expect([row.model, row.session_id, row.branch, row.commit_sha]).toEqual([null, null, null, null]);
  });
});
