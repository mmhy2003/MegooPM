/**
 * Syntax-checks the JavaScript in a custom page.
 *
 * Done in the browser on purpose. The alternatives were measured and both
 * fail: the pure-Python parsers available to the backend reject half of
 * modern JavaScript (optional chaining, class fields, logical assignment),
 * and a real engine binding publishes no aarch64 wheel, so it would not
 * install on ARM deployments. The browser already has a perfect, current
 * parser on every architecture — and it is the exact engine that will run the
 * page, so it cannot disagree with production.
 *
 * `new Function(body)` compiles the body and throws `SyntaxError` on bad
 * code. It never calls it, so nothing in the page executes here: a generated
 * `fetch`, redirect or storage write stays inert. Do not swap this for `eval`.
 */

export interface ScriptProblem {
  /** 1-based position among the page's inline scripts. */
  index: number;
  message: string;
  /** The script body, so the fault can be found in the document. */
  source: string;
}

const SCRIPT_PATTERN = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;

/**
 * The inline script bodies in ``html``.
 *
 * A `src`-only script has nothing to parse, and a custom page must not have
 * one anyway — the page is served with no internet.
 */
export function extractScripts(html: string): string[] {
  const bodies: string[] = [];
  for (const match of html.matchAll(SCRIPT_PATTERN)) {
    const body = match[2];
    if (body.trim()) bodies.push(body);
  }
  return bodies;
}

/** Every script whose syntax the browser rejects. Empty means all parse. */
export function checkScripts(html: string): ScriptProblem[] {
  const problems: ScriptProblem[] = [];
  extractScripts(html).forEach((source, i) => {
    try {
      // Compile only. See the module note: this must never become a call.
      new Function(source);
    } catch (err) {
      // A non-syntax throw would mean the browser could not tell us anything
      // useful; report what it said either way rather than guessing.
      problems.push({
        index: i + 1,
        message: err instanceof Error ? err.message : String(err),
        source: source.trim(),
      });
    }
  });
  return problems;
}

/** The faults as an instruction for whoever has to fix them. */
export function describeScriptProblems(problems: ScriptProblem[]): string {
  if (problems.length === 0) return "";
  const lines = [
    `The page has ${problems.length} script(s) that will not parse. ` +
      `Fix the JavaScript and change nothing else.`,
  ];
  for (const problem of problems) {
    lines.push(`\nScript ${problem.index} — ${problem.message}\n${problem.source}`);
  }
  return lines.join("\n");
}
