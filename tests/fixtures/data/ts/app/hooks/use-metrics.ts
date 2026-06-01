import { useMemo } from "react";

// A verbatim copy of lib/format.ts:formatBytes — should be the shared util, not re-rolled.
function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

export function useFormattedTotal(bytes: number): string {
  return useMemo(() => formatBytes(bytes), [bytes]);
}
