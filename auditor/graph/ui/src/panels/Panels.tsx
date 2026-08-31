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
  flexShrink: 0,
  gap: "12px",
  minHeight: 0,
  overflowY: "auto",
  width: "340px",
};

/** Spec 12.1's right-hand column, and the one component `App.tsx` has to know about.
 *
 * The three reachable pages branch here, before any child can spin: `graph serve` and a bare
 * `pnpm dev` draw the inlined graph with no live chrome at all, and the daemon's no-repo page
 * draws the switcher over an explicit empty state rather than a stream nothing will ever poll.
 */
export default function Panels({ live }: { live: LiveGraph }) {
  const mode = panelMode(live.boot);
  if (mode === "static") return null;
  return (
    <aside className="anim-panels" style={column}>
      <Chrome
        status={live.status}
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
