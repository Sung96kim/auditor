import { useEffect, useState } from "react";

/** Below this the live column stops taking its 340 px off the canvas and stacks under it.
 *
 * 1100 px, because the two fixed side panels and the column together leave the canvas nothing
 * under about that: an ordinary half-screen split on a 1920 display is 960.
 */
export const NARROW = "(max-width: 1100px)";

function read(query: string): boolean {
  return typeof window !== "undefined" && Boolean(window.matchMedia?.(query).matches);
}

/** Whether the viewport matches, kept current. False wherever there is no media engine at all,
 * which is jsdom and any prerender: the wide layout is the safe thing to assume. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => read(query));
  useEffect(() => {
    const list = window.matchMedia?.(query);
    if (!list) return;
    setMatches(list.matches);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}
