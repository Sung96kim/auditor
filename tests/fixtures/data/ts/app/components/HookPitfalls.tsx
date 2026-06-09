import { useEffect, useState } from "react";

// Deliberate React hook pitfalls (precision fixtures): an async effect callback whose returned
// promise silently replaces the cleanup function, eager state init that re-runs the parse on
// every render, and a freshly generated key that remounts each row. Trips
// TS-REACT-ASYNC-EFFECT / TS-REACT-EAGER-STATE-INIT / TS-REACT-RANDOM-KEY.
export function HookPitfalls({ items }: { items: string[] }) {
  const [prefs] = useState(JSON.parse(localStorage.getItem("prefs")));

  useEffect(async () => {
    await fetch("/api/sync");
  }, []);

  return (
    <ul aria-label="items">
      {items.map((it) => (
        <li key={Math.random()}>
          {it}-{prefs}
        </li>
      ))}
    </ul>
  );
}
