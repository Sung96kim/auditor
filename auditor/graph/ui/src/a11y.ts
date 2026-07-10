import type { KeyboardEvent } from "react";

/**
 * Keyboard activation for non-native elements given a button role: fire `handler` on
 * Enter/Space so keyboard users can operate a clickable <div>/<span> the same way mouse
 * users do. Pair with literal `role="button"` and `tabIndex={0}` on the element.
 */
export function onEnterOrSpace(handler: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handler();
    }
  };
}
