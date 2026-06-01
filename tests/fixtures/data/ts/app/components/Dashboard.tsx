import { useState, useEffect, useMemo, useReducer } from "react";
import { useCallback, useRef } from "react";

import { fetchMetrics } from "../lib/api";
import { StatCard } from "./StatCard";

type Metric = { id: string; label: string; value: number };
type SortKey = "label" | "value";

interface FilterState {
  query: string;
  sort: SortKey;
  desc: boolean;
}

function filterReducer(state: FilterState, patch: Partial<FilterState>): FilterState {
  return { ...state, ...patch };
}

// A page component that has accreted far too much: many hooks, a reducer, derived/sorted
// state, a deep layout pyramid, an inline pure helper, and a second component in the file.
export function Dashboard() {
  const [range, setRange] = useState("1h");
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [filter, dispatch] = useReducer(filterReducer, { query: "", sort: "label", desc: false });
  const lastRange = useRef(range);

  useEffect(() => {
    lastRange.current = range;
    setLoading(true);
    fetchMetrics(range)
      .then(setMetrics)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [range]);

  const visible = useMemo(() => {
    const matched = metrics.filter((m) => m.label.includes(filter.query));
    const sorted = [...matched].sort((a, b) =>
      filter.sort === "label" ? a.label.localeCompare(b.label) : a.value - b.value,
    );
    return filter.desc ? sorted.reverse() : sorted;
  }, [metrics, filter]);

  const onPick = useCallback((id: string) => setSelected(id), []);

  // Pure formatter that closes over no component state — belongs in lib/.
  function describe(point: Metric): string {
    const pct = Math.round(point.value * 100);
    return `${point.label}: ${pct}%`;
  }

  if (error) {
    return <div role="alert">Failed to load: {error}</div>;
  }

  return (
    <main>
      <nav>
        {["1h", "24h", "7d"].map((r, i) => (
          <button key={i} onClick={() => setRange(r)}>
            {r}
          </button>
        ))}
      </nav>
      <section>
        <div>
          <div>
            <div>
              <div>
                <div>
                  <span>{loading ? "Loading…" : `${visible.length} metrics`}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section>
        {visible.map((m) => (
          <StatCard
            key={m.id}
            label={describe(m)}
            value={m.value}
            unit="ratio"
            delta={0}
            tone="neutral"
            href={`/m/${m.id}`}
            loading={loading}
            onSelect={onPick}
          />
        ))}
      </section>
      <DashboardFooter expanded={expanded} onToggle={() => setExpanded(!expanded)} />
    </main>
  );
}

function DashboardFooter({ expanded, onToggle }: { expanded: boolean; onToggle: () => void }) {
  return <footer onClick={onToggle}>{expanded ? "less" : "more"}</footer>;
}
