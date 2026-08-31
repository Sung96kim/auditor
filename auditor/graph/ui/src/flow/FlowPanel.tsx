import { Empty } from "../panels/States";

/** Spec 12.1's flow mode. Task 6 gives it the toggle, the slider and the hubs. */
export default function FlowPanel({ base, repo }: { base: string; repo: string }) {
  void base;
  void repo;
  return (
    <div data-testid="FlowPanel">
      <Empty what="a flow" hint="search for a symbol to walk its callers or its callees" />
    </div>
  );
}
