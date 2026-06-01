import { useState } from "react";
import { useEffect } from "react";

export function Panel() {
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(true), []);
  return (
    <div onClick={() => setOpen(!open)}>
      <Button>
        <CloseIcon />
      </Button>
      <img src="hero.png" />
      <span tabIndex={4}>focus me first</span>
    </div>
  );
}

export function Sidebar() {
  return <nav>nav</nav>;
}
