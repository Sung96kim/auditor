import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render } from "@testing-library/react";
import RunnerMark, { MARKS, markFor } from "./RunnerMark";

afterEach(cleanup);

describe("the runner column's mark", () => {
  it("renders the vendored svg, never initials and never a text badge", () => {
    const { container } = render(<RunnerMark runner="claude" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg!.getAttribute("aria-label")).toBe("Claude");
    // the mark's own <title> is its accessible name; nothing outside it may be text
    const outside = container.cloneNode(true) as HTMLElement;
    outside.querySelectorAll("svg").forEach((el) => el.remove());
    expect(outside.textContent?.trim()).toBe("");
    expect(svg!.querySelector("title")?.textContent).toBe("Claude");
  });

  it("codex gets its own mark and its own label", () => {
    const { container } = render(<RunnerMark runner="codex" />);
    expect(container.querySelector("svg")!.getAttribute("aria-label")).toBe("Codex");
  });

  it("the mark inherits currentColor rather than carrying its own fill", () => {
    const { container } = render(<RunnerMark runner="claude" />);
    expect(container.querySelector("svg")!.getAttribute("fill")).toBe("currentColor");
  });

  it("the path data is the whole mark, so nothing is drawn from a font or a glyph", () => {
    const { container } = render(<RunnerMark runner="claude" />);
    const path = container.querySelector("path");
    expect(path).not.toBeNull();
    expect(path!.getAttribute("d")).toBe(MARKS.claude.d);
  });

  it("the runner name is case insensitive, because the wire spells it lowercase", () => {
    expect(markFor("Claude")).toBe(markFor("claude"));
  });

  it("an unknown runner degrades to a dot rather than to its own name in text", () => {
    const { container } = render(<RunnerMark runner="gemini" />);
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).not.toContain("gemini");
  });

  it("the two runners the spec names are both present and no others", () => {
    expect(Object.keys(MARKS).sort()).toEqual(["claude", "codex"]);
  });
});
