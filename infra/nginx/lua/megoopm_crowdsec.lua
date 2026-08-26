-- CrowdSec bouncer — per proxy-host access handler (MEG-22).
--
-- Attached only to server blocks the backend renders with the CrowdSec toggle
-- on (see backend app/templates/nginx/server.conf.j2 -> `access_by_lua_file`).
-- Two enforcement layers, each independently controllable per host:
--
--   1. IP remediation — always, when this file runs: if the client IP carries
--      an active decision (a ban/captcha/throttle pulled from LAPI), the
--      bouncer applies it and terminates the request here.
--   2. Inline AppSec / WAF — only when the server set `$megoopm_crowdsec_appsec`
--      to "on" (the backend renders this from the per-host AppSec toggle): the
--      request is forwarded to the AppSec component for payload inspection and
--      blocked if it matches a WAF rule.
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

-- 1) IP-level remediation (bans, captchas, throttles). `Allow` performs the
--    lookup and, on a hit, applies the remediation and exits the request.
local ok, err = pcall(function()
    csmod.Allow(ip)
end)
if not ok then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec IP check error for ", ip, ": ", tostring(err))
    -- Fall through: IP check failing open is preferable to blocking all traffic.
end

-- 2) Inline AppSec / WAF, per-host. Only invoked when this server opted in.
if ngx.var.megoopm_crowdsec_appsec == "on" and csmod.AppSecCheck then
    local appsec_ok, appsec_err = pcall(function()
        csmod.AppSecCheck(ip)
    end)
    if not appsec_ok then
        ngx.log(ngx.ERR, "[megoopm] CrowdSec AppSec error for ", ip, ": ", tostring(appsec_err))
        -- APPSEC_FAILURE_ACTION in crowdsec-bouncer.conf governs fail-open vs
        -- fail-closed inside the module; on a Lua-level error we log and let the
        -- request proceed so a WAF glitch cannot black-hole an entire host.
    end
end
