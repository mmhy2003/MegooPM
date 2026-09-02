# The default site over TLS — design

## Goal

Serve the operator's chosen default site for HTTPS requests to names MegooPM
holds a certificate for but has no enabled host for — most obviously, a host
the operator has just disabled.

## Why

Disabling a proxy host today sends its visitors to **a different, unrelated
enabled host**.

The loader filters to enabled hosts (`loader.py`, `ProxyHost.enabled.is_(True)`),
so a disabled host contributes no server block at all and its name no longer
exists in nginx. What answers then depends on the port:

- The base config declares `listen 80 default_server`, so **HTTP** correctly
  reaches the default site.
- Nothing declares a default server on **443**. When nginx has no default for
  an address:port it falls back to the *first server block it loaded* for that
  port — whichever managed vhost sorts first in the `conf.d/*.conf` glob.

Measured in the real `megoopm-nginx` image, with one disabled name and two
enabled hosts:

| request | answered by |
| --- | --- |
| `http://disabled.example` | the default site |
| `https://disabled.example` | `aaa.example` — an unrelated enabled host |

Renaming the files so a different host sorted first moved the fallback with it,
confirming the mechanism is configuration order rather than anything about the
host. So which site a visitor lands on can change simply because someone adds a
host whose name sorts earlier.

It is worse than it looks when certificates overlap. If the disabled name and
the host that answers are covered by the same wildcard or multi-SAN
certificate, the wrong site's certificate still validates for the name typed —
so there is no browser warning and the substitution is silent.

This was a known gap, recorded under Non-goals in
`2026-09-01-default-site-design.md`, deferred because closing it appeared to
require a generated self-signed certificate. It does not: the operator's own
certificates already cover the names that matter.

## Non-goals

- **Names covered by no certificate.** Someone pointing an unrelated domain at
  the server still reaches an arbitrary enabled host. Closing that needs a
  self-signed certificate (nothing else can terminate TLS for a name never
  configured) and its regeneration story, and every visitor to such a name
  would meet a certificate warning before the default site. Explicitly still
  deferred; this design narrows the hole to names the operator has no
  certificate for.
- **A per-host "this host is disabled" page.** The default site is what the
  operator configured for unmatched requests; a disabled host is exactly that.
- **Changing what the default site *is*.** The mode matrix (congratulations /
  404 / 444 / redirect / custom page) is unchanged and is reused as-is.
- **Runtime certificate selection.** See Approaches.

## Decisions taken during brainstorming

**Every active certificate participates, not only wildcards.** The operator
chose this over a wildcard-only rule. It covers the reported case directly: a
disabled host holding its own single-name certificate is served the default
site over that certificate. Wildcards are not special-cased — a wildcard name
covers its subdomains through ordinary nginx matching.

**Certificate-based coverage only**, without the self-signed backstop (see
Non-goals). Chosen so that every visitor to a covered name gets a valid
certificate and no warning, and so this change introduces no key generation.

### Approaches considered

| approach | cert per name | validated by `nginx -t` | cost |
| --- | --- | --- | --- |
| **one server block per certificate** | at render time | **yes** | one template |
| `ssl_certificate_by_lua_block` | at handshake | no | Lua on every handshake, own cache-invalidation story |
| `listen 443` on the existing default server | one, fixed | yes | breaks with two domains |

**One server block per certificate** was chosen. The certificates are all known
at render time, so nothing needs runtime selection; keeping the decision in
declarative config means `nginx -t` validates it before it is ever applied,
which is what the whole apply/rollback path depends on.

## Behaviour

For each **active** certificate, emit one `listen 443 ssl` server block whose
`server_name` lists the names that certificate covers minus the names an
enabled host already claims on 443. The block serves the default site.

Verified in the real image, with a catch-all for `*.example.com` alongside an
enabled host `aaa.example.com`, each holding a *different* certificate so the
served certificate identifies which block answered:

| request | serves | certificate |
| --- | --- | --- |
| `aaa.example.com` (enabled) | its own host | its own |
| `disabled.example.com` | **the default site** | **the wildcard's** |
| `deep.sub.example.com` | the default site | the wildcard's |
| `example.com` (apex) | wrong host — not matched by `*.example.com` | — |
| `unrelated.other.tld` | wrong host — no certificate covers it | — |

Two properties this establishes:

- **An exact `server_name` beats a wildcard**, so enabled hosts are unaffected.
  The catch-all file in the probe was named to sort *last*, so it won on name
  matching rather than on configuration order.
- **A wildcard matches multiple leading labels**, so `deep.sub.example.com` is
  covered.

The apex row is not a defect of this design: `*.example.com` does not match
bare `example.com`, and the fix is already inherent — the block's names come
from **every name in the certificate**, and a Let's Encrypt wildcard is
normally issued for `example.com` and `*.example.com` together, so the apex is
covered whenever the certificate covers it.

## Data flow

**`state.py`**

```python
@dataclass(frozen=True, slots=True)
class DefaultTlsSpec:
    certificate: CertificateSpec
    server_names: tuple[str, ...]
```

and `DesiredState.default_tls: tuple[DefaultTlsSpec, ...] = ()`.

**`loader.py`** builds the tuple: read active certificates, subtract the
claimed names, assign each surviving name to exactly one certificate, and drop
certificates left with none.

**`renderer.py`** emits `megoopm-default-tls-{cert_id}.conf` from
`render_config`, which is already a reconciliation target for `conf.d` — so a
certificate that is deleted or falls out of `active` has its block removed by
the existing reconciler. **No new directory, and no change to
`infra/nginx/nginx.conf`.**

**`default_tls.conf.j2`** renders the block:

```nginx
# Managed by MegooPM — do not edit by hand.
server {
    listen 443 ssl;
    server_name {{ names }};

    # Without this a request matching no location falls through to OpenResty's
    # compiled-in root and is served its welcome page.
    root /var/empty/megoopm;

    # cert-material {{ cert.id }}:{{ cert.fingerprint }}
    ssl_certificate {{ cert.fullchain_path }};
    ssl_certificate_key {{ cert.privkey_path }};

    include {{ default_dir }}/*.conf;
}
```

The `include` is the *existing* default-site fragment — a bare `location /`,
already written by the default-site feature. So the mode chosen in Settings
applies to HTTPS with no duplicated logic, and if no default site is
configured, no fragment exists, no location matches and nginx answers 404 —
the same degradation the `:80` default server has.

The `cert-material` comment is copied from the host template deliberately.
Renewal rewrites certificate files in place, leaving paths — and therefore the
rendered config — byte-identical; without this line the engine's idempotency
check sees no change, skips the reload, and every node keeps serving the old
certificate from memory. That bug has already been fixed once for host blocks
and must not be reintroduced here.

`http2` is not enabled on these blocks. It is a per-host option and this is a
fallback page; leaving it off keeps the block free of a setting that has no
host to come from.

## The three rules

**Only `active` certificates.** A `pending` certificate has no files on disk;
referencing one makes `nginx -t` fail, which rolls back the *entire* apply. As
this feature selects certificates that no operator explicitly attached to a
host, a single queued ACME order for an unrelated domain could otherwise break
every configuration change on the instance — a much worse failure than the bug
being fixed. `failed` and `expired` are excluded for the same reason and
because an expired certificate helps no visitor.

**Exclude only names claimed on 443.** Duplicate `server_name`s conflict only
within the same listen port, so a name claimed by an enabled host that renders
a 443 block (proxy, redirection or dead host with a certificate) is excluded,
and nothing else is. A host that is enabled but HTTP-only therefore *gains*
coverage: its name currently has no `:443` block at all, so HTTPS to it lands
on a stranger's site, and after this change it shows the default site.

**Names and blocks are emitted in sorted order** — names sorted within a
block, blocks by certificate id. Two nodes rendering the same state must
produce byte-identical text, or the engine sees a spurious change and reloads
every node for nothing.

**Each name string appears in exactly one block.** Where two certificates list
the *identical* name, the lowest certificate id wins. Two blocks declaring one
name would leave nginx picking one arbitrarily — the same class of bug this
design exists to remove — and would emit a conflicting-server-name warning.

Note this arbitration is only needed for identical strings. An exact name in
one certificate and a wildcard covering it in another are *different* strings,
so they never collide: both blocks are emitted and nginx prefers the exact one,
which is the behaviour the probe above confirmed. No specificity ranking is
required in the loader, and inventing one would only risk diverging from what
nginx actually does.

## Testing

**The loader's name arithmetic is pure and carries the risk**, so it takes the
bulk of the coverage:

- a disabled host's name becomes covered
- an enabled TLS host's name never becomes covered
- an enabled HTTP-only host's name does become covered
- a wildcard covers a subdomain, and the apex only when the certificate lists it
- the identical name listed by two certificates lands in exactly one block,
  chosen by lowest id
- an exact name in one certificate and a wildcard covering it in another are
  both emitted, since nginx arbitrates them
- `pending`, `failed` and `expired` certificates contribute nothing
- a certificate whose every name is claimed produces no file at all
- names are emitted in a deterministic order, so two nodes render identical text

**Rendering** covers the block's shape: the certificate paths, the
`cert-material` line changing when the fingerprint changes, and the default-site
include.

**Against real nginx**, in the `megoopm-nginx` image: an exact enabled host
still wins over a catch-all covering the same domain, and an unclaimed name
under that certificate is served the default site. This is the assertion that
cannot be made against a string.

**Not covered by automated tests:** whether a browser shows no warning. That
follows from serving the operator's own certificate for the name requested, and
is a manual check.

## Files

- `app/services/nginx/state.py` — `DefaultTlsSpec`, `DesiredState.default_tls`
- `app/services/nginx/loader.py` — build the specs
- `app/services/nginx/renderer.py` — emit `megoopm-default-tls-{id}.conf`
- `app/templates/nginx/default_tls.conf.j2` (new)
- `backend/tests/test_nginx_default_tls.py` (new), plus additions to the
  existing loader and render tests

No frontend change: the setting this serves already exists.

## Open risks

**A certificate marked `active` whose files are missing on disk** still breaks
the apply. The status column is a proxy for "material exists", and this design
trusts it exactly as the host path already does. It is a pre-existing coupling,
not one introduced here, but this feature widens its blast radius from "hosts
using that certificate" to "any apply while that row is active".

**An operator may not expect a disabled host to answer at all.** Disabling
currently produces a wrong-site response, so any defined behaviour is an
improvement, but "disabled" now means "shows the default site" rather than
"unreachable". A host that must be unreachable should use the `444` default
site mode, or be deleted.
