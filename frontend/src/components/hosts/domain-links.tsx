"use client";

/**
 * A host's domain names, linked so an operator can open one to check it.
 *
 * Shared by the proxy / redirection / 404 host lists, which all render the same
 * cell.
 *
 * The scheme comes from whether a certificate is attached: nginx only listens
 * on `:443` for a host that has one, so linking to https without a certificate
 * would reliably fail to connect.
 *
 * A wildcard has no single address behind it, so it renders as plain text. The
 * alternatives — substituting `www`, or stripping to the apex — both invent a
 * hostname that was never configured and may not resolve or may not be covered
 * by the certificate.
 */
export function DomainLinks({
  domains,
  secure,
}: {
  domains: string[];
  /** True when the host has a certificate, so it answers on :443. */
  secure: boolean;
}) {
  const scheme = secure ? "https" : "http";
  return (
    <div className="flex flex-wrap gap-1">
      {domains.map((domain) =>
        domain.includes("*") ? (
          <span key={domain}>{domain}</span>
        ) : (
          <a
            key={domain}
            href={`${scheme}://${domain}`}
            target="_blank"
            // Without noopener the opened page holds a reference to this one
            // and can navigate it; a proxy host can point anywhere.
            rel="noopener noreferrer"
            className="hover:underline"
          >
            {domain}
          </a>
        ),
      )}
    </div>
  );
}
