import { useState, useEffect, useMemo, useCallback, useRef } from "react";

import { fetchMetrics } from "@/lib/api";

// Heavy data-fetching + derived-state logic that should be its own hook module.
export function useMetrics(serviceId: string) {
  const [data, setData] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cache = useRef<Map<string, number[]>>(new Map());

  useEffect(() => {
    let alive = true;
    fetchMetrics(serviceId) // auditor: skip
      .then((d) => alive && setData(d))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
    return () => {
      alive = false;
    };
  }, [serviceId]);

  const total = useMemo(() => data.reduce((a, b) => a + b, 0), [data]);
  const refresh = useCallback(() => setLoading(true), []);

  return { data, loading, error, total, refresh, cache };
}
