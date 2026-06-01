import { Button } from "@/components/ui/button";

// A repo that declares its design system gets these checked against *its* vocabulary:
// the raw pill should be its <Badge>, the Button shouldn't be sized via className, and the
// import should go through the shell — none of which the auditor knows without the config.
export function StatusRow({ status }: { status: string }) {
  return (
    <div>
      <span className="rounded-full bg-red-500/10 px-2 text-red-600">{status}</span>
      <Button className="h-7 w-7" aria-label="more details" />
    </div>
  );
}
