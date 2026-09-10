/** The object nginx's error points at, and where it is managed. */
export interface Culprit {
  label: string;
  href: string;
}

/**
 * The managed object named by an `nginx -t` failure, if it names one.
 *
 * nginx cites the file and line, and every file the engine writes carries the
 * object's database id: `megoopm-proxy-12.conf` is proxy host 12 — the id every
 * list now shows in its first column. A default-TLS file is named for the
 * certificate it serves. Output that names no managed file returns null rather
 * than a guess: sending an operator to the wrong object is worse than none.
 */
export function culpritOf(output: string): Culprit | null {
  const match = /megoopm-(proxy|redirect|dead|upstream|stream|default-tls)-(\d+)\.conf/.exec(
    output,
  );
  if (!match) return null;
  const [, kind, id] = match;
  switch (kind) {
    case "proxy":
      return { label: `Proxy host #${id}`, href: "/proxy-hosts" };
    case "redirect":
      return { label: `Redirection host #${id}`, href: "/redirection-hosts" };
    case "dead":
      return { label: `404 host #${id}`, href: "/dead-hosts" };
    case "upstream":
      return { label: `Upstream pool #${id}`, href: "/upstreams" };
    case "stream":
      return { label: `Stream #${id}`, href: "/streams" };
    default:
      return { label: `The default TLS site for certificate #${id}`, href: "/certificates" };
  }
}
