export function clampPercent(value: number) {
  if (value < 0) return 0;
  if (value > 100) return 100;
  return Math.round(value);
}

export function bytesToKib(n: number) {
  const v = n / 1024;
  return v.toFixed(2);
}

export function bytesToMib(n: number) {
  const v = n / 1048576;
  return v.toFixed(2);
}
