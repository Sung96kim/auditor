import { useState } from "react";

import { Badge, Button, Card } from "../lib/ui";

// Every block below is a deliberate *near-miss*: it resembles something a detector flags but
// is correct, so the auditor must stay silent. This file MUST produce zero findings — it is
// the precision (false-positive) guard for the TS detectors.
export function EdgeCases({ rows }: { rows: Array<{ id: string; name: string; ok: boolean }> }) {
  const [open, setOpen] = useState(false);

  // sanitized constant markup — dangerouslySetInnerHTML with a *constant* is not flagged
  const legal = <div dangerouslySetInnerHTML={{ __html: "<b>Terms</b>" }} />;

  return (
    <Card>
      {legal}

      {/* a real <button>, not a div masquerading as one */}
      <Button onClick={() => setOpen(!open)} aria-label="toggle">
        <ChevronIcon aria-hidden />
      </Button>

      {/* rounded-full bg-X/10 wrapping ONLY an icon — a backdrop disc, not a status pill */}
      <div className="size-10 rounded-full bg-emerald-500/10">
        <ShieldIcon aria-hidden />
      </div>

      {/* div with onClick but proper role + keyboard support */}
      <div role="button" tabIndex={0} onClick={() => setOpen(true)} onKeyDown={() => setOpen(true)}>
        expand
      </div>

      {/* anchor with a real href + safe rel on target=_blank */}
      <a href="/docs" target="_blank" rel="noopener noreferrer">
        Docs
      </a>

      {/* decorative image with explicit empty alt is valid */}
      <img src="divider.svg" alt="" />

      {/* iframe with a title is fine */}
      <iframe src="/embed" title="metrics embed" />

      {/* a list already mapped over data with stable keys — not "repeated JSX" */}
      <ul>
        {rows.map((row) => (
          <li key={row.id}>
            <span>{row.name}</span>
            <Badge tone={row.ok ? "ok" : "error"}>{row.ok ? "up" : "down"}</Badge>
          </li>
        ))}
      </ul>
    </Card>
  );
}
