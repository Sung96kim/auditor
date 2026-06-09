import { useEffect, useState } from "react";

// Deliberate React hook pitfalls (precision fixtures): an async effect callback whose returned
// promise silently replaces the cleanup function, eager state init that re-runs the parse on
// every render, and a freshly generated key that remounts each row. Trips
// TS-REACT-ASYNC-EFFECT / TS-REACT-EAGER-STATE-INIT / TS-REACT-RANDOM-KEY.
export function HookPitfalls({ rows }: { rows: string[] }) {
  const [cache] = useState(JSON.parse(sessionStorage.getItem("cache")));

  useEffect(async () => {
    await fetch("/api/refresh");
  }, []);

  return (
    <ul aria-label="rows">
      {rows.map((row) => (
        <li key={Date.now()}>
          {row}-{cache}
        </li>
      ))}
    </ul>
  );
}
