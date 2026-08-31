import { useEffect, useState } from "react";

/** The value once it has stopped moving, so a keystroke is not a server-side graph walk.
 *
 * Parameterised on the delay rather than one hook per caller: the flow symbol box was issuing
 * one `/api/flow` per character, each a full traversal.
 */
export function useDebounced<T>(value: T, ms = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
