import { describe, expect, it } from "vitest";

import {
  DATA_URI_PATTERN,
  IMAGE_WARN_BYTES,
  STARTER_HTML,
  base64Bytes,
  dataUriSummary,
  describeImageSize,
  formatBytes,
  htmlByteLength,
  imgTagFor,
  isOverPageCap,
} from "@/components/custom-pages/lib";

describe("formatBytes", () => {
  it("keeps small sizes in bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(847)).toBe("847 B");
  });
  it("switches to KB and MB with one decimal", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(4300)).toBe("4.2 KB");
    expect(formatBytes(2 * 1024 * 1024)).toBe("2.0 MB");
  });
});

describe("htmlByteLength", () => {
  it("counts encoded bytes, not characters", () => {
    expect(htmlByteLength("abc")).toBe(3);
    // A multi-byte character costs more than one byte on the wire, which is
    // what the server's cap actually measures.
    expect(htmlByteLength("é")).toBe(2);
    expect(htmlByteLength("🎉")).toBe(4);
  });
});

describe("isOverPageCap", () => {
  it("is false at the cap and true past it", () => {
    expect(isOverPageCap("x".repeat(2 * 1024 * 1024))).toBe(false);
    expect(isOverPageCap("x".repeat(2 * 1024 * 1024 + 1))).toBe(true);
  });
});

describe("base64Bytes", () => {
  it("measures the decoded payload of a data URI", () => {
    // "AAAA" decodes to 3 bytes.
    expect(base64Bytes("data:image/png;base64,AAAA")).toBe(3);
  });
  it("accounts for padding", () => {
    expect(base64Bytes("data:image/png;base64,AA==")).toBe(1);
    expect(base64Bytes("data:image/png;base64,AAA=")).toBe(2);
  });
  it("returns 0 for something that is not a data URI", () => {
    expect(base64Bytes("/logo.png")).toBe(0);
  });
});

describe("describeImageSize", () => {
  it("passes a small image without comment", () => {
    expect(describeImageSize(1024)).toBeNull();
  });
  it("warns once an image would bloat the source", () => {
    const warning = describeImageSize(IMAGE_WARN_BYTES + 1);
    expect(warning).toContain("KB");
    // Base64 inflates by ~4/3, which is the whole reason for the warning.
    expect(warning).toMatch(/larger|inflat/i);
  });
});

describe("imgTagFor", () => {
  it("builds an img tag with the data URI and an alt from the filename", () => {
    expect(imgTagFor("logo.png", "data:image/png;base64,AAAA")).toBe(
      '<img src="data:image/png;base64,AAAA" alt="logo">',
    );
  });
  it("escapes a filename that would break out of the attribute", () => {
    const tag = imgTagFor('evil".png', "data:image/png;base64,AAAA");
    expect(tag).not.toContain('alt="evil"');
    expect(tag).toContain("&quot;");
  });
});

describe("dataUriSummary", () => {
  it("replaces the payload with its type and decoded size", () => {
    expect(dataUriSummary("data:image/png;base64,AAAA")).toBe("data:image/png (3 B)");
  });
});

describe("DATA_URI_PATTERN", () => {
  it("matches the base64 payload inside a document", () => {
    const html = '<img src="data:image/png;base64,AAAABBBB"> and <p>text</p>';
    const found = html.match(new RegExp(DATA_URI_PATTERN.source, "g"));
    expect(found).toEqual(["data:image/png;base64,AAAABBBB"]);
  });
  it("does not match a plain url", () => {
    expect(new RegExp(DATA_URI_PATTERN.source).test('<img src="/logo.png">')).toBe(false);
  });
});

describe("STARTER_HTML", () => {
  it("is a complete document a new page can be edited from", () => {
    expect(STARTER_HTML).toContain("<!doctype html>");
    expect(STARTER_HTML).toContain("</html>");
  });
});
