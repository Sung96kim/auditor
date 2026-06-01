import { useMemo } from "react";

import { formatBytes } from "../lib/format";

interface StatCardProps {
  label: string;
  value: number;
  unit: "bytes" | "count" | "ratio";
  delta: number;
  tone: "neutral" | "up" | "down";
  href: string;
  loading: boolean;
  onSelect: (label: string) => void;
}

// A realistic, feature-heavy card whose prop list has crept past what one component should
// own — the kind of "kitchen-sink" signature that should be grouped into sub-objects.
export function StatCard({
  label,
  value,
  unit,
  delta,
  tone,
  href,
  loading,
  onSelect,
}: StatCardProps) {
  const display = useMemo(() => {
    if (unit === "bytes") return formatBytes(value);
    if (unit === "ratio") return `${Math.round(value * 100)}%`;
    return value.toLocaleString();
  }, [unit, value]);

  return (
    <article className="stat" data-tone={tone} onClick={() => onSelect(label)}>
      <header>
        <span>{label}</span>
      </header>
      <strong>{loading ? "…" : display}</strong>
      <small>{delta >= 0 ? `+${delta}` : delta}</small>
      <a href={href}>details</a>
    </article>
  );
}
