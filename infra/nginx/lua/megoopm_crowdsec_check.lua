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
local unmarking -- the module whose Allow() already clears the mark

-- The live log's request line says `cs: "denied"` from a mark set before
-- Allow() (read in the log phase, log_by_lua in nginx.conf). Allow() ends
-- every request itself and never returns: a denial through ban/captcha/
-- challenge.apply, an allowed request with ngx.exit(ngx.DECLINED) (or, after a
-- solved captcha, ngx.redirect). So Allow gets an `ngx` of its own whose exit
-- with DECLINED and redirect clear the mark first. The apply functions live in
-- other modules and keep the real ngx, so a denial keeps the mark.
local function clear_mark_when_allowed(csmod)
    if unmarking == csmod then
        return
    end
    local real = ngx
    local own_ngx = setmetatable({
        exit = function(code)
            if code == real.DECLINED then
                real.ctx.mgm_cs = nil
            end
            return real.exit(code)
        end,
        redirect = function(...)
            real.ctx.mgm_cs = nil
            return real.redirect(...)
        end,
    }, { __index = real, __newindex = real })
    local env = getfenv(csmod.Allow)
    setfenv(csmod.Allow, setmetatable({ ngx = own_ngx }, { __index = env, __newindex = env }))
    unmarking = csmod
end

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
    clear_mark_when_allowed(csmod)

    local ip = ngx.var.remote_addr
    -- Marked before Allow(); see clear_mark_when_allowed. The line after the
    -- call runs only when Allow returns or raises, and then nothing was denied.
    ngx.ctx.mgm_cs = "denied"
    local ok, err = pcall(function()
        csmod.Allow(ip)
    end)
    ngx.ctx.mgm_cs = nil
    if not ok then
        -- Failing open on a Lua-level error beats blocking every host. AppSec's
        -- own posture on an AppSec-backend error is APPSEC_FAILURE_ACTION in
        -- crowdsec-bouncer.conf, inside the module.
        ngx.log(ngx.ERR, "[megoopm] CrowdSec check error for ", ip, ": ", tostring(err))
    end
end

return M
