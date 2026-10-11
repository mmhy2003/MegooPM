import { describe, expect, it } from "vitest";

import { formatBytes, triggerLabel } from "@/components/backups/lib";

describe("formatBytes", () => {
  it("scales to the unit a person would say out loud", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB");
  });

  it("says zero rather than nothing", () => {
    expect(formatBytes(0)).toBe("0 B");
  });
});

describe("triggerLabel", () => {
  it("names how a backup came to exist", () => {
    expect(triggerLabel("manual")).toBe("Manual");
    expect(triggerLabel("auto")).toBe("Automatic");
    expect(triggerLabel("upload")).toBe("Uploaded");
  });
});
