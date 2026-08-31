export const NODE_COLOR: Record<string, string> = {
  class: "#B57BFF",
  file: "#5B9BFF",
  function: "#46C98B",
  method: "#EC6F9E",
  module: "#F0A848",
};

export const THEME = {
  bgApp: "#0B0E14",
  bgCanvas: "#090B11",
  bgPanel: "#0E121B",
  bgElevated: "#161C28",
  border: "#1B2230",
  accent: "#7C7CFF",
};

/** The text ramp the panels read from. `label` and `faint` clear WCAG AA on both panel surfaces. */
export const TEXT = {
  value: "#e2e8f0",
  strong: "#cbd5e1",
  body: "#94a3b8",
  label: "#7a8ba3",
  faint: "#7a8ba3",
};

/** The four tones a live surface reports in: a healthy loop, a warning, a failure, a pause. */
export const TONE = {
  ok: "#46C98B",
  busy: THEME.accent,
  warn: "#f59e0b",
  bad: "#ef4444",
  idle: TEXT.label,
};

/** The same four tones as a panel wash, so a state box is a state and not a bordered paragraph. */
export const TONE_WASH: Record<keyof typeof TONE, string> = {
  ok: "rgba(70, 201, 139, 0.08)",
  busy: "rgba(124, 124, 255, 0.09)",
  warn: "rgba(245, 158, 11, 0.09)",
  bad: "rgba(239, 68, 68, 0.09)",
  idle: "rgba(122, 139, 163, 0.06)",
};
