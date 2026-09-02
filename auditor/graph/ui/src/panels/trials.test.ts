import { describe, it, expect } from "vitest";
import { trialLine } from "./trials";
import { tuningRow } from "../api/wire.fixture";

describe("trialLine", () => {
  it("names the word and what the trial did to the clustering", () => {
    expect(trialLine(tuningRow())).toBe(
      "helper (pending): clusters 24 to 22, name-edge churn 0.4%, label churn 25.0%",
    );
  });

  it("says a row no trial has looked at is not measured", () => {
    const row = tuningRow();
    const line = trialLine({
      ...row,
      metrics: { ...row.metrics, measured_at: 0 },
    });
    expect(line).toBe("helper (pending): not measured yet");
  });

  it("puts the guard that refused where the numbers would have gone", () => {
    const row = tuningRow({ status: "rejected" });
    const line = trialLine({
      ...row,
      metrics: { ...row.metrics, refused: "singleton clusters 6 to 9" },
    });
    expect(line).toBe("helper (rejected): refused, singleton clusters 6 to 9");
  });

  it("reads the accepted status off the row rather than the metrics", () => {
    expect(trialLine(tuningRow({ status: "active" }))).toContain("helper (active)");
  });
});
