import type { LiveGraph } from "../api/useLiveGraph";
import { Empty } from "./States";

/** Spec 12.1's run stream. Task 5 gives it the ten columns and the collapsed rows. */
export default function RunStream({ live }: { live: LiveGraph }) {
  void live;
  return (
    <div data-testid="RunStream">
      <Empty what="runs" hint="the observer has not started a refinement run for this repo yet" />
    </div>
  );
}
