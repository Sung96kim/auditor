export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

export function toKib(n: number): string {
  const v = n / 1024;
  return v.toFixed(1);
}

export function toMib(n: number): string {
  const v = n / 1048576;
  return v.toFixed(1);
}

export function evaluate(expr: string): number {
  return eval(expr);
}
