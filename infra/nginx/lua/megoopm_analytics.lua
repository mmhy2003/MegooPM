-- Visitor analytics: count in shared memory, push to Redis on a timer.
--
-- WHY NOT REDIS DIRECTLY FROM THE LOG PHASE
--
-- Cosockets are unavailable in `log_by_lua`. Measured, not assumed: a probe
-- that logs a line then calls ngx.socket.tcp() in that phase prints the line
-- and never reaches the connect — the handler is aborted. So a Redis call from
-- the log phase silently counts nothing.
--
-- Instead the log phase touches only `lua_shared_dict`, which is plain shared
-- memory and always available: two `incr` calls, no socket, no timer per
-- request. A recurring timer in one worker drains that dict into Redis.
--
-- This is also cheaper than the original design would have been: high traffic
-- costs two shm increments per request instead of a network round trip, and
-- the pushes are batched by construction.

local M = {}

local DICT = "megoopm_visits"

local ok_conf, conf = pcall(require, "megoopm_analytics_conf")
if not ok_conf then
    -- The entrypoint generates that file. Without it, counting is simply off.
    conf = { enabled = false }
end

local function dict()
    return ngx.shared[DICT]
end

-- UTC, NOT ngx.today(): that returns the container's LOCAL date, while the
-- flush task builds its key from datetime.now(UTC). Under a non-UTC clock the
-- two would disagree for part of each day and the counters would land on a key
-- nothing drains -- vanishing at TTL with no error anywhere.
local function utc_day()
    return os.date("!%Y-%m-%d")
end

-- --- request path ----------------------------------------------------------

function M.log()
    if not conf.enabled then
        return
    end

    local shm = dict()
    if not shm then
        return
    end

    -- Never count MegooPM's own traffic. The dashboard scrapes stub_status
    -- every 15s and the healthcheck hits /healthz; without this the instance
    -- would be the busiest visitor it has.
    if conf.status_port and ngx.var.server_port == conf.status_port then
        return
    end
    if ngx.var.uri == "/healthz" then
        return
    end

    -- remote_addr, not the forwarded header: real_ip has already rewritten
    -- this from the trusted proxy ranges. Reading the header directly would
    -- let any client forge its own address, and every count with it.
    local ip = ngx.var.remote_addr
    if not ip or ip == "" then
        return
    end

    local day = utc_day()
    -- init=0 so the first increment creates the key rather than failing.
    shm:incr("c|" .. day .. "|" .. ip, 1, 0)
    shm:incr("b|" .. day .. "|" .. ip, tonumber(ngx.var.bytes_sent) or 0, 0)
end

-- --- drain -----------------------------------------------------------------

local function connect()
    local ok_mod, redis = pcall(require, "resty.redis")
    if not ok_mod then
        return nil, "resty.redis unavailable"
    end
    local red = redis:new()
    -- Short: this runs in a timer, not a request, but a hung Redis should not
    -- pile timers up either.
    red:set_timeouts(500, 500, 500)
    local ok, err = red:connect(conf.host, conf.port)
    if not ok then
        return nil, err
    end
    return red
end

function M.drain()
    local shm = dict()
    if not shm or not conf.enabled then
        return
    end

    local keys = shm:get_keys(0) -- 0 = all
    if not keys or #keys == 0 then
        return
    end

    local red, err = connect()
    if not red then
        -- Leave the counts in shm: the next tick retries and nothing is lost
        -- while Redis is briefly away.
        ngx.log(ngx.DEBUG, "[megoopm] analytics: redis unavailable: ", tostring(err))
        return
    end

    red:init_pipeline()
    local pushed = 0
    for _, key in ipairs(keys) do
        local value = shm:get(key)
        if value and value > 0 then
            -- Subtract exactly what was read rather than deleting the key:
            -- increments arriving between the get and here are preserved,
            -- because they add on top of the remainder.
            shm:incr(key, -value)

            local kind, day, ip = key:match("^(%a)|([%d-]+)|(.+)$")
            if kind then
                local hash = conf.prefix
                    .. (kind == "c" and ":count:" or ":bytes:")
                    .. day
                red:hincrby(hash, ip, value)
                red:expire(hash, conf.ttl)
                pushed = pushed + 1
            end
        end
    end

    if pushed == 0 then
        red:cancel_pipeline()
        red:set_keepalive(10000, 32)
        return
    end

    local _, commit_err = red:commit_pipeline()
    if commit_err then
        -- The counts were already subtracted, so this batch is lost. Accepted:
        -- these are approximate visitor analytics, not billing, and holding
        -- them for a retry would need a second buffer to leak instead.
        ngx.log(ngx.WARN, "[megoopm] analytics: push failed: ", tostring(commit_err))
    end
    red:set_keepalive(10000, 32)
end

-- Started from init_worker_by_lua. One worker only: the dict is shared across
-- workers, so every worker draining it would multiply the connections for no
-- extra throughput.
function M.start()
    if not conf.enabled then
        return
    end
    if ngx.worker.id() ~= 0 then
        return
    end
    local interval = conf.push_interval or 1
    local ok, err = ngx.timer.every(interval, function(premature)
        if premature then
            return
        end
        pcall(M.drain)
    end)
    if not ok then
        ngx.log(ngx.ERR, "[megoopm] analytics: timer failed to start: ", tostring(err))
    end
end

return M
