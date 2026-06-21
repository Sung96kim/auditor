export interface TweenState {
  active: boolean;
  startTime: number;
  duration: number;
  progress: number;
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function clamp(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x;
}

export function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

export function makeTween(duration: number): TweenState {
  return { active: true, startTime: performance.now(), duration, progress: 0 };
}

export function tickTween(tween: TweenState, now: number): TweenState {
  if (!tween.active) return tween;
  const raw = (now - tween.startTime) / tween.duration;
  const progress = clamp(raw, 0, 1);
  const active = progress < 1;
  return { ...tween, progress, active };
}
