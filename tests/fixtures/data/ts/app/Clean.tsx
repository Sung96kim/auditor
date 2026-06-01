import { useState } from "react";

interface Props {
  title: string;
  onClose: () => void;
}

export function Panel({ title, onClose }: Props) {
  const [open, setOpen] = useState(true);
  return (
    <section aria-label={title}>
      <button type="button" aria-label="close" onClick={onClose}>
        <CloseIcon aria-hidden />
      </button>
      <p>{open ? title : "hidden"}</p>
      <img src="logo.png" alt="company logo" />
      <a href="/docs" target="_blank" rel="noopener noreferrer">
        Docs
      </a>
    </section>
  );
}
