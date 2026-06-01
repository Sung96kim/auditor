import { useState } from "react";

export function Panel() {
  const [open, setOpen] = useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => setOpen(!open)}
      onKeyDown={() => setOpen(!open)}
    >
      <Button aria-label="close">
        <CloseIcon />
      </Button>
      <img src="hero.png" alt="hero" />
      <span tabIndex={0}>focusable</span>
    </div>
  );
}
