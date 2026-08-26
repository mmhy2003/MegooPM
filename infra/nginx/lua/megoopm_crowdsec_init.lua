-- CrowdSec bouncer — one-time initialisation (MEG-22).
--
-- Referenced from the http{} context of infra/nginx/nginx.conf via
-- `init_by_lua_file`. It loads the stock CrowdSec OpenResty bouncer module once
-- per worker-master and hands it the config file rendered from the environment.
--
-- Enforcement itself is NOT wired globally: it is attached per proxy host by
-- lua/megoopm_crowdsec.lua (`access_by_lua_file`), so a host with the CrowdSec
-- toggle off is never touched by the bouncer. This split is what makes the
-- feature toggleable per host (the MEG-22 acceptance criterion).

local ok, csmod = pcall(require, "crowdsec")
if not ok then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer module not found: ", tostring(csmod))
    return
end

-- Config path is baked by the Dockerfile; the container entrypoint has already
-- substituted ${CROWDSEC_*} placeholders from the environment.
local conf = "/etc/nginx/crowdsec-bouncer.conf"
local init_ok, err = csmod.init(conf, "megoopm-nginx-bouncer/v1.0")
if not init_ok then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer init failed: ", tostring(err))
    return
end

-- Expose the initialised module to the per-host access handler.
_G.megoopm_crowdsec = csmod
ngx.log(ngx.NOTICE, "[megoopm] CrowdSec bouncer initialised (", conf, ")")
