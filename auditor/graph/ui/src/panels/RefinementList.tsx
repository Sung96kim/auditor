import { Empty } from "./States";

/** Spec 12.1's refinement list by status. Task 5 gives it the groups. */
export default function RefinementList({ base, repo }: { base: string; repo: string }) {
  void base;
  void repo;
  return (
    <div data-testid="RefinementList">
      <Empty what="refinements" hint="no refinement has been proposed for this repo yet" />
    </div>
  );
}
