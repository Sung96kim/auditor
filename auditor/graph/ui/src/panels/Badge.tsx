import { TONE, TONE_WASH } from "../theme";
import { stateTone } from "./chrome";
import { mono } from "./Panel";

/** Spec 12.1's state badge: the selected repo's loop state, coloured by what that state means. */
export default function Badge({ state }: { state: string }) {
  const tone = stateTone(state);
  return (
    <span
      style={{
        background: TONE_WASH[tone],
        border: `1px solid ${TONE[tone]}55`,
        borderRadius: "999px",
        color: TONE[tone],
        fontFamily: mono.fontFamily,
        fontSize: "10.5px",
        padding: "2px 8px",
        whiteSpace: "nowrap",
      }}
    >
      {state}
    </span>
  );
}
