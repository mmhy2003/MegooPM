// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildId } from "@/lib/build-id";

let root: string;

function write(path: string, text: string) {
  const full = join(root, path);
  mkdirSync(join(full, ".."), { recursive: true });
  writeFileSync(full, text);
}

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "build-id-"));
  write("package-lock.json", "{}");
  write("next.config.ts", "export default {}");
  write("src/app/page.tsx", "export default function Page() {}");
  write("public/logo.png", "png");
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

const ENV = { NEXT_PUBLIC_API_BASE_URL: "https://api.example.com" };

describe("buildId", () => {
  it("is the same for the same source, as on every node of an HA setup", () => {
    const first = buildId(root, ENV);
    expect(first).toMatch(/^[0-9a-f]{16}$/);
    expect(buildId(root, { ...ENV })).toBe(first);
  });

  it("changes when a source file changes", () => {
    const before = buildId(root, ENV);
    write("src/app/page.tsx", "export default function Page() { return 1 }");
    expect(buildId(root, ENV)).not.toBe(before);
  });

  it("changes when a file is added under public", () => {
    const before = buildId(root, ENV);
    write("public/new.svg", "<svg/>");
    expect(buildId(root, ENV)).not.toBe(before);
  });

  it("changes when a value inlined into the bundle changes", () => {
    const before = buildId(root, ENV);
    expect(buildId(root, { NEXT_PUBLIC_API_BASE_URL: "https://other.example.com" })).not.toBe(before);
  });

  it("ignores environment values that are not inlined into the bundle", () => {
    expect(buildId(root, { ...ENV, HOSTNAME: "node-b" })).toBe(buildId(root, ENV));
  });

  it("does not depend on the order files are listed in", () => {
    const before = buildId(root, ENV);
    rmSync(join(root, "src"), { recursive: true });
    write("src/z.ts", "z");
    write("src/a.ts", "a");
    const once = buildId(root, ENV);
    rmSync(join(root, "src"), { recursive: true });
    write("src/a.ts", "a");
    write("src/z.ts", "z");
    expect(buildId(root, ENV)).toBe(once);
    expect(once).not.toBe(before);
  });
});
