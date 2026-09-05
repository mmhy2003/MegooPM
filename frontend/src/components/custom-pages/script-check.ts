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

/**
 * The API's cap on an assist instruction, mirroring MAX_INSTRUCTION_CHARS in
 * the backend's page_assist service. A repair goes out as an instruction, so a
 * report longer than this is rejected before the model ever sees it.
 */
export const MAX_INSTRUCTION_CHARS = 2000;

/**
 * How much of one script to quote. The model is sent the whole document
 * anyway; the quote only has to say which script is meant.
 */
const EXCERPT_CHARS = 400;

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

function excerpt(source: string, budget: number): { text: string; shortened: boolean } {
  const limit = Math.max(80, Math.min(EXCERPT_CHARS, budget));
  if (source.length <= limit) return { text: source, shortened: false };
  return { text: source.slice(0, limit), shortened: true };
}

/**
 * The faults as an instruction for whoever has to fix them.
 *
 * Bounded to {@link MAX_INSTRUCTION_CHARS}. Quoting a real page's script whole
 * produced a 4,000-character instruction that the API rejected outright, so
 * every repair failed validation instead of fixing anything.
 */
export function describeScriptProblems(problems: ScriptProblem[]): string {
  if (problems.length === 0) return "";
  const header =
    `The page has ${problems.length} script(s) that will not parse. ` +
    `Fix the JavaScript and change nothing else.`;

  const parts: string[] = [header];
  let budget = MAX_INSTRUCTION_CHARS - header.length;

  problems.forEach((problem, i) => {
    const head = `\nScript ${problem.index} - ${problem.message}`;
    const remaining = problems.length - i;
    if (budget - head.length < 80) return;
    // Share what is left between the faults still to come, so several broken
    // scripts cannot overflow the cap between them.
    const share = Math.floor((budget - head.length) / remaining);
    const { text, shortened } = excerpt(problem.source, share);
    // Say so when the quote is partial: otherwise the model reads an excerpt
    // as the whole script and "fixes" an ending that was never missing.
    const body = shortened ? `${text}\n... (shortened; the full script is in the page)` : text;
    const block = `${head}\n${body}`;
    parts.push(block);
    budget -= block.length;
  });

  return parts.join("\n").slice(0, MAX_INSTRUCTION_CHARS);
}
