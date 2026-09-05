import { describe, expect, it } from "vitest";

import {
  checkScripts,
  describeScriptProblems,
  extractScripts,
  MAX_INSTRUCTION_CHARS,
} from "@/components/custom-pages/script-check";

/** A script far longer than any instruction the API will accept. */
function longScript(lines: number): string {
  return Array.from({ length: lines }, (_, i) => `  const v${i} = ${i};`).join("\n");
}

describe("extractScripts", () => {
  it("finds an inline script body", () => {
    const html = "<html><body><script>const a = 1;</script></body></html>";
    expect(extractScripts(html)).toEqual(["const a = 1;"]);
  });

  it("finds every script on the page", () => {
    const html = "<script>const a = 1;</script><p>x</p><script>const b = 2;</script>";
    expect(extractScripts(html)).toHaveLength(2);
  });

  it("keeps script attributes out of the body", () => {
    const html = '<script type="module" defer>const a = 1;</script>';
    expect(extractScripts(html)).toEqual(["const a = 1;"]);
  });

  it("ignores a script that only loads a src", () => {
    // Nothing to parse, and a custom page should not have one anyway.
    expect(extractScripts('<script src="x.js"></script>')).toEqual([]);
  });

  it("ignores an empty script", () => {
    expect(extractScripts("<script>\n\n</script>")).toEqual([]);
  });

  it("finds nothing in a page with no scripts", () => {
    expect(extractScripts("<html><body><h1>Hi</h1></body></html>")).toEqual([]);
  });
});

describe("checkScripts", () => {
  const MODERN = [
    ["optional chaining", "const a = obj?.b?.c;"],
    ["nullish coalescing", "const a = x ?? 'd';"],
    ["class fields", "class A { count = 0; }"],
    ["private field", "class A { #secret = 1; }"],
    ["async/await", "async function f(){ await g(); }"],
    ["template literal", "const f = (n) => `count: ${n}`;"],
    ["spread", "const a = {...b, c: 1};"],
    ["optional catch", "try { f(); } catch { g(); }"],
    ["logical assignment", "let a = 0; a ||= 1;"],
    ["browser globals", "document.getElementById('x').textContent = window.location.href;"],
    ["regex literal", "const re = /ab+c/gi;"],
  ] as const;

  it.each(MODERN)("accepts modern syntax: %s", (_label, code) => {
    // A false positive is the expensive failure here: it would send the model
    // "fixing" correct code and leave the page worse than unchecked.
    expect(checkScripts(`<script>${code}</script>`)).toEqual([]);
  });

  const BROKEN = [
    ["missing brace", "function f() { if (a) { return 1; }"],
    ["stray paren", "const a = (1 + 2));"],
    ["unterminated string", "const a = 'oops;"],
    ["bad keyword", "cnst a = 1"],
    ["unclosed template", "const a = `hi ${x;"],
  ] as const;

  it.each(BROKEN)("reports broken syntax: %s", (_label, code) => {
    const problems = checkScripts(`<script>${code}</script>`);
    expect(problems).toHaveLength(1);
    expect(problems[0].message).toBeTruthy();
  });

  it("names which script failed when there are several", () => {
    const html = "<script>const a = 1;</script><script>const b = (;</script>";
    const problems = checkScripts(html);
    expect(problems).toHaveLength(1);
    expect(problems[0].index).toBe(2);
  });

  it("carries the offending source so the fix can be located", () => {
    const problems = checkScripts("<script>const a = (1 + 2));</script>");
    expect(problems[0].source).toContain("const a =");
  });

  it("never runs the code it is checking", () => {
    // Validation must not execute a generated fetch, redirect or storage write.
    const w = window as unknown as Record<string, unknown>;
    delete w.__sideEffectRan;
    checkScripts("<script>window.__sideEffectRan = true;</script>");
    expect(w.__sideEffectRan).toBeUndefined();
  });

  it("says nothing about a page with no scripts", () => {
    expect(checkScripts("<h1>Hi</h1>")).toEqual([]);
  });
});

describe("describeScriptProblems", () => {
  it("reads as an instruction naming the error and the code", () => {
    const text = describeScriptProblems(checkScripts("<script>const a = (1 + 2));</script>"));
    expect(text).toMatch(/script/i);
    expect(text).toContain("const a =");
  });

  it("stays within the instruction the API will accept", () => {
    // The repair goes out as an assist instruction, which the API caps at 2000
    // characters. A real page's script is far longer than that, so quoting one
    // whole made every repair fail validation instead of fixing anything.
    const big = longScript(500);
    const text = describeScriptProblems(checkScripts(`<script>${big}\nconst a = (1 + 2));</script>`));

    expect(big.length).toBeGreaterThan(MAX_INSTRUCTION_CHARS);
    expect(text.length).toBeLessThanOrEqual(MAX_INSTRUCTION_CHARS);
  });

  it("says when it has shortened the code it quotes", () => {
    // Otherwise the model reads an excerpt as the whole script and "fixes" an
    // ending that was never missing.
    const text = describeScriptProblems(
      checkScripts(`<script>${longScript(500)}\nconst a = (1 + 2));</script>`),
    );
    expect(text).toMatch(/shortened|truncat|excerpt/i);
  });

  it("still names the error after shortening", () => {
    const text = describeScriptProblems(
      checkScripts(`<script>${longScript(500)}\nconst a = (1 + 2));</script>`),
    );
    expect(text).toMatch(/script 1/i);
  });

  it("quotes a short script in full", () => {
    const text = describeScriptProblems(checkScripts("<script>const a = (1 + 2));</script>"));
    expect(text).toContain("const a = (1 + 2));");
    expect(text).not.toMatch(/shortened/i);
  });

  it("stays within the cap even with several broken scripts", () => {
    const one = longScript(300);
    const html = `<script>${one}\nconst x = (;</script><script>${one}\nconst y = (;</script>`;
    expect(describeScriptProblems(checkScripts(html)).length).toBeLessThanOrEqual(
      MAX_INSTRUCTION_CHARS,
    );
  });
});
