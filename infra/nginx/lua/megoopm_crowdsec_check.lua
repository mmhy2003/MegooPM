-- CrowdSec bouncer check, shared by every server block that enforces bans.
--
-- Two entry points call it: megoopm_crowdsec.lua (generated hosts, in the
-- access or server-rewrite phase) and the base default server's inline block,
-- which exempts /healthz. The stock bouncer's `Allow()` applies any IP decision
-- and, when AppSec is configured, forwards the request to the WAF; on a hit it
-- ends the request itself.
--
-- It fails open: if the module never initialised (see
-- megoopm_crowdsec_init.lua), requests are allowed. That is logged once per
-- worker, not per request — the default sites call this for every scanner hit,
-- and a line per hit would bury every other error.
local M = {}

local warned = false

function M.check()
    local csmod = _G.megoopm_crowdsec
    if not csmod then
        if not warned then
            warned = true
            ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer not initialised; allowing requests",
                " (logged once per worker)")
        end
        return
    end

    local ip = ngx.var.remote_addr
    local ok, err = pcall(function()
        csmod.Allow(ip)
    end)
    if not ok then
        -- Failing open on a Lua-level error beats blocking every host. AppSec's
        -- own posture on an AppSec-backend error is APPSEC_FAILURE_ACTION in
        -- crowdsec-bouncer.conf, inside the module.
        ngx.log(ngx.ERR, "[megoopm] CrowdSec check error for ", ip, ": ", tostring(err))
    end
end

return M
