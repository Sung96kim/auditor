import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RunnerMark, { MARKS, markFor, markName } from "./RunnerMark";

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

describe("the mark's box", () => {
  it("the mark is its own block, so a table cell does not reserve a text descender under it", () => {
    const { container } = render(<RunnerMark runner="claude" />);
    expect((container.querySelector("svg") as SVGElement).style.display).toBe("block");
  });

  it("an unknown runner's stand-in fills the same box, so a column of marks stays aligned", () => {
    const { container } = render(<RunnerMark runner="gemini" size={13} />);
    const stand = container.firstElementChild as HTMLElement;
    expect(stand.style.width).toBe("13px");
    expect(stand.style.height).toBe("13px");
    expect(stand.getAttribute("aria-label")).toBe("unknown runner gemini");
  });
});

describe("the two runners the wire serves with no mark of their own", () => {
  it.each([
    ["none", "no runner"],
    ["fake", "test runner"],
  ])("%s is named %s rather than reported as a mystery", (runner, name) => {
    // both are ordinary `RunnerKind` members: every assessment-only row carries `none`
    expect(markName(runner)).toBe(name);
    render(<RunnerMark runner={runner} />);
    expect(screen.getByRole("img", { name }).textContent).toBe("-");
  });

  it("a runner the enum has never had is still called unknown", () => {
    expect(markName("gemini")).toBe("unknown runner gemini");
  });
});
