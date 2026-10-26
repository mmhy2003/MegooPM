import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * The ID a production build is given (next.config.ts), from what goes into
 * the bundle: the source, the public files, the locked dependencies, and the
 * `NEXT_PUBLIC_*` values Next inlines.
 *
 * Derived, not a timestamp: every node of an HA setup builds its own image,
 * and nodes built from the same source must agree. Two IDs that differ for
 * one app would make Next reload the page on every navigation that lands on
 * the other node, and the update notice would never go away.
 *
 * Build-time only (Node's fs): never import it from app code.
 */
export function buildId(root: string, env: Record<string, string | undefined>): string {
  const hash = createHash("sha256");
  for (const file of inputs(root)) {
    // The path too: a renamed file is a different build.
    hash.update(relative(root, file).split(sep).join("/"));
    hash.update("\0");
    hash.update(readFileSync(file));
    hash.update("\0");
  }
  const inlined = Object.keys(env)
    .filter((key) => key.startsWith("NEXT_PUBLIC_"))
    .sort();
  for (const key of inlined) hash.update(`${key}=${env[key] ?? ""}\0`);
  return hash.digest("hex").slice(0, 16);
}

const FILES = ["package-lock.json", "next.config.ts"];
const DIRS = ["src", "public"];

/** Every input file, in one order whatever order the disk lists them in. */
function inputs(root: string): string[] {
  const out = FILES.map((f) => join(root, f)).filter((f) => existsSync(f));
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else out.push(path);
    }
  };
  for (const dir of DIRS) if (existsSync(join(root, dir))) walk(join(root, dir));
  return out.sort();
}
