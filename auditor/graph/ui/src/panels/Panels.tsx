import type { LiveGraph } from "../api/useLiveGraph";
import { panelMode } from "./chrome";
import { Empty } from "./States";
import Chrome from "./Chrome";
import RunStream from "./RunStream";
import RefinementList from "./RefinementList";
import FlowPanel from "../flow/FlowPanel";

const column: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flexGrow: 0,
  flexShrink: 0,
  gap: "12px",
  minHeight: 0,
  overflowY: "auto",
};

/** A rail beside the canvas, which is what there is room for above the breakpoint. */
const RAIL: React.CSSProperties = { ...column, flexBasis: "340px", width: "340px" };

/** A shelf under the canvas. 340 px taken out of a 640 px window is the canvas's whole width,
 * and the canvas reaching zero is what used to take the entire page into the error boundary. */
const SHELF: React.CSSProperties = { ...column, maxHeight: "45%", width: "100%" };

/** Spec 12.1's right-hand column, and the one component `App.tsx` has to know about.
 *
 * The three reachable pages branch here, before any child can spin: `graph serve` and a bare
 * `pnpm dev` draw the inlined graph with no live chrome at all, and the daemon's no-repo page
 * draws the switcher over an explicit empty state rather than a stream nothing will ever poll.
 */
export default function Panels({
  live,
  narrow = false,
}: {
  live: LiveGraph;
  narrow?: boolean;
}) {
  const mode = panelMode(live.boot);
  if (mode === "static") return null;
  return (
    <aside className="anim-panels" style={narrow ? SHELF : RAIL}>
      <Chrome
        status={live.status}
        base={live.boot.base}
        repo={live.boot.repo}
        onChooseRepo={live.chooseRepo}
        onRetry={live.retry}
      />
      {mode === "no repo" ? (
        <Empty what="repo chosen" hint="pick a repo above to see its runs, refinements and flow" />
      ) : (
        <>
          <RunStream live={live} />
          <RefinementList base={live.boot.base} repo={live.boot.repo} />
          <FlowPanel base={live.boot.base} repo={live.boot.repo} />
        </>
      )}
    </aside>
  );
}
