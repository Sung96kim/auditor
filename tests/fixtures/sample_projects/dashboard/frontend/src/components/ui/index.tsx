import type { ReactNode } from "react";

export function Button({
  children,
  size = "md",
}: {
  children: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  return <button className={`btn btn-${size}`}>{children}</button>;
}

export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <span className={`rounded-full bg-${tone}-500/10 px-2 py-0.5 text-${tone}-700`}>
      {children}
    </span>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return <section className="card rounded-lg border p-4">{children}</section>;
}
