import { describe, expect, it } from "vitest";

import { culpritOf } from "@/components/nginx-status/lib";

const failed = (file: string) =>
  `nginx: [emerg] host not found in upstream "myapp:8080" in /data/nginx/conf.d/${file}:31\n` +
  "nginx: configuration file /etc/nginx/nginx.conf test failed";

describe("culpritOf", () => {
  it("names a proxy host by the id in its file name", () => {
    // The file name carries the database id, which every list now shows.
    expect(culpritOf(failed("megoopm-proxy-12.conf"))).toEqual({
      label: "Proxy host #12",
      href: "/proxy-hosts",
    });
  });

  it("names every kind of object the engine writes", () => {
    expect(culpritOf(failed("megoopm-redirect-3.conf"))?.label).toBe("Redirection host #3");
    expect(culpritOf(failed("megoopm-dead-4.conf"))?.label).toBe("404 host #4");
    expect(culpritOf(failed("megoopm-upstream-5.conf"))?.label).toBe("Upstream pool #5");
    expect(culpritOf(failed("stream/megoopm-stream-6.conf"))?.label).toBe("Stream #6");
    expect(culpritOf(failed("megoopm-default-tls-7.conf"))).toEqual({
      label: "The default TLS site for certificate #7",
      href: "/certificates",
    });
  });

  it("names nothing when the output points at no managed file", () => {
    // A base-config error, or a message nginx phrased without a path. Guessing
    // would send an operator to the wrong object.
    expect(culpritOf("nginx: [emerg] unexpected end of file")).toBeNull();
    expect(culpritOf("")).toBeNull();
  });
});
