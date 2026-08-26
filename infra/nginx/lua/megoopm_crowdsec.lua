-- CrowdSec bouncer — per proxy-host access handler (MEG-22).
--
-- Attached only to server blocks the backend renders with the CrowdSec toggle
-- on (see backend app/templates/nginx/server.conf.j2 -> `access_by_lua_file`).
-- So bouncer enforcement itself IS per host: a host with `crowdsec_enabled` off
-- never references this file and is untouched.
--
-- What runs here, via the stock bouncer's `Allow()`:
--   1. IP remediation — if the client IP carries an active decision (ban /
--      captcha / throttle pulled from LAPI in stream mode), it is applied and
--      the request is terminated here.
--   2. Inline AppSec / WAF — `Allow()` ALSO forwards the request to the AppSec
--      component whenever AppSec is configured globally (APPSEC_URL set in
--      crowdsec-bouncer.conf). See the AppSec scope note below.
--
-- AppSec scope (MEG-32 / defect 5c770e72 D3): AppSec is currently a GLOBAL
-- on/off, not per host. lua-cs-bouncer v1.0.8 runs AppSec *inside* `Allow()`
-- whenever APPSEC_URL is set, with no per-call switch, so once the AppSec engine
-- is wired every `crowdsec_enabled` host is inspected. The per-host
-- `crowdsec_appsec_enabled` flag (rendered as `$megoopm_crowdsec_appsec`) is
-- therefore NOT honoured yet — it is retained as a reserved marker so genuine
-- per-host AppSec can be reintroduced without an API/schema change. Calling
-- `AppSecCheck()` here again would only double-inspect the same request, so it
-- is intentionally omitted. See docs/crowdsec.md.
--
-- The handler fails safe: if the bouncer module never initialised (see
-- megoopm_crowdsec_init.lua), it logs and allows the request rather than 500ing
-- every proxied host.

local csmod = _G.megoopm_crowdsec
if not csmod then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer not initialised; allowing request")
    return
end

local ip = ngx.var.remote_addr

-- `Allow` performs the IP-decision lookup and (when AppSec is configured) the
-- AppSec inspection, applying any remediation and exiting the request on a hit.
local ok, err = pcall(function()
    csmod.Allow(ip)
end)
if not ok then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec check error for ", ip, ": ", tostring(err))
    -- Fall through: failing open on a Lua-level error is preferable to blocking
    -- all traffic to the host. AppSec's own fail-open/closed posture on an
    -- AppSec-backend error is governed by APPSEC_FAILURE_ACTION in
    -- crowdsec-bouncer.conf, inside the module.
end
