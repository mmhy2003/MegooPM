import { describe, expect, it } from "vitest";

import {
  DATA_URI_PATTERN,
  MAX_ASSIST_BYTES,
  elideImages,
  isOverAssistCap,
  restoreImages,
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

/* -------------------------------------------------------------------------- */
/* The image round trip                                                        */
/* -------------------------------------------------------------------------- */

const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg";
const JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQ";

describe("elideImages", () => {
  it("leaves a document with no images untouched", () => {
    const html = "<!doctype html><html><body><h1>hi</h1></body></html>";
    expect(elideImages(html)).toEqual({ html, images: [] });
  });

  it("replaces each data URI with a placeholder that is still a data URI", () => {
    const { html, images } = elideImages(`<img src="${PNG}">`);
    // Still a well-formed URI: a model shown a malformed src repairs it.
    expect(html).toBe('<img src="data:image/png;base64,MEGOOPM_IMAGE_1">');
    expect(images).toEqual([PNG]);
  });

  it("preserves each image's own mime type", () => {
    const { html } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    expect(html).toContain("data:image/png;base64,MEGOOPM_IMAGE_1");
    expect(html).toContain("data:image/jpeg;base64,MEGOOPM_IMAGE_2");
  });

  it("shrinks the document by the weight of the images", () => {
    const big = `data:image/png;base64,${"A".repeat(100_000)}`;
    const { html } = elideImages(`<img src="${big}">`);
    expect(html.length).toBeLessThan(100);
  });
});

describe("restoreImages", () => {
  it("round-trips a document byte for byte", () => {
    const original = `<body><img src="${PNG}"><p>x</p><img src="${JPEG}"></body>`;
    const { html, images } = elideImages(original);
    const restored = restoreImages(html, images);
    expect(restored.html).toBe(original);
    expect(restored.warnings).toEqual([]);
  });

  it("round-trips a document with no images", () => {
    const original = "<p>nothing here</p>";
    const { html, images } = elideImages(original);
    expect(restoreImages(html, images).html).toBe(original);
  });

  it("accepts a dropped placeholder and says how many went", () => {
    // The instruction may legitimately have asked for the image to go.
    const { images } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    const returned = '<img src="data:image/jpeg;base64,MEGOOPM_IMAGE_2">';
    const restored = restoreImages(returned, images);
    expect(restored.html).toBe(`<img src="${JPEG}">`);
    expect(restored.warnings).toEqual(["1 image was removed from the page."]);
  });

  it("pluralises the removal note", () => {
    const { images } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    const restored = restoreImages("<p>gone</p>", images);
    expect(restored.warnings).toEqual(["2 images were removed from the page."]);
  });

  it("leaves a placeholder it never sent, so the break is visible", () => {
    // Stripping the <img> would be a silent structural edit nobody asked for.
    const { images } = elideImages(`<img src="${PNG}">`);
    const returned = '<img src="data:image/png;base64,MEGOOPM_IMAGE_9">';
    const restored = restoreImages(returned, images);
    expect(restored.html).toContain("MEGOOPM_IMAGE_9");
    expect(restored.warnings).toContain(
      "The result referenced 1 image that isn't in your page.",
    );
  });

  it("reports both problems at once", () => {
    const { images } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    const returned = '<img src="data:image/png;base64,MEGOOPM_IMAGE_7">';
    expect(restoreImages(returned, images).warnings).toHaveLength(2);
  });
});

describe("isOverAssistCap", () => {
  it("is false at the cap and true past it", () => {
    expect(isOverAssistCap("x".repeat(MAX_ASSIST_BYTES))).toBe(false);
    expect(isOverAssistCap("x".repeat(MAX_ASSIST_BYTES + 1))).toBe(true);
  });

  it("measures encoded bytes, not characters", () => {
    expect(isOverAssistCap("é".repeat(MAX_ASSIST_BYTES))).toBe(true);
  });
});
