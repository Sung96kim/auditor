import { describe, it, expect } from "vitest";
import { STATIC, readBootstrap } from "./bootstrap";

describe("readBootstrap", () => {
  it("graph serve injects nothing, so the page stays static and never polls", () => {
    expect(readBootstrap({})).toEqual(STATIC);
  });

  it("the daemon's flag turns live mode on at first paint", () => {
    expect(readBootstrap({ __AUDITOR_OBSERVER__: { live: true, base: "/", repo: "/w" } }))
      .toEqual({ live: true, base: "/", repo: "/w" });
  });

  it("the daemon's no-repo page is live with no repo chosen, not static", () => {
    const boot = readBootstrap({ __AUDITOR_OBSERVER__: { live: true, base: "/", repo: "" } });
    expect(boot.live).toBe(true);
    expect(boot.repo).toBe("");
  });

  it("anything but an explicit true is static", () => {
    expect(readBootstrap({ __AUDITOR_OBSERVER__: {} })).toEqual(STATIC);
  });
});
