-- Live logs relay: nginx's live log lines in, Redis pub/sub out, nothing kept.
--
-- Every server block sends its request and error lines, as syslog over UDP,
-- to 127.0.0.1:5140 -- the stream server in nginx.conf that runs this module,
-- once per datagram. Each line is classified, tagged with this node's id and
-- PUBLISHed to `megoopm:live:<host key>`, but only while that channel has a
-- subscriber (a Live logs page watching that host); otherwise it is dropped on
-- arrival. Redis is a pass-through here: no key is ever written.
--
-- Nothing here may slow or fail nginx. The request that produced a line was
-- already served -- syslog over UDP is fire and forget -- so the worst a fault
-- can do is lose a line. Every Redis call has a 200 ms timeout and runs under
-- pcall, and an unreachable Redis means the line is dropped.

local M = {}

-- A private instance, so these settings never leak to other cjson users.
local cjson = require("cjson.safe").new()
-- "/" stays "/" in published URLs; it is valid JSON either way.
pcall(cjson.encode_escape_forward_slash, false)
local null = require("cjson.safe").null

local ok_conf, conf = pcall(require, "megoopm_live_conf")
if not ok_conf or type(conf) ~= "table" then
    -- The entrypoint generates that file. Without it, the relay drops everything.
    conf = { enabled = false }
end

local DICT = "megoopm_live"
local CHANNEL = "megoopm:live:"
-- How long a subscriber count is trusted. A busy host costs one NUMSUB per
-- second while nobody watches, and a page that starts watching sees lines
-- within a second.
local NUMSUB_TTL = 1
local TIMEOUT_MS = 200

-- nginx's syslog header without the hostname (`nohostname`): PRI, the BSD
-- timestamp (day padded with a space), then the tag `mgm_<host key>`.
local HEADER = "^<(%d+)>%a+ +%d+ [%d:]+ mgm_([%w]+): (.*)$"
-- Error lines at warn and above are kept (syslog severity 0..4).
local WARN = 4
-- nginx logs these TLS failures at info, but an admin chasing a handshake
-- problem needs them.
local KEEP_AT_INFO = {
    "SSL_do_handshake() failed",
    "peer closed connection in SSL handshake",
}
local DENIED = "[Crowdsec] denied"

-- Pure: no I/O, no ngx state. Returns nil for anything that is not shown.
--   { host, log, sev, body, truncated, f }
-- `f` is the decoded request/stream line; absent when the body is not JSON.
-- A body that starts as JSON but does not parse was cut short by nginx's
-- syslog line limit (a long URL): it is kept, raw, as `truncated`.
function M.parse(datagram)
    if type(datagram) ~= "string" then
        return nil
    end
    local pri, host, body = datagram:match(HEADER)
    if not pri then
        return nil
    end
    local sev = tonumber(pri) % 8

    -- Access lines are JSON objects (megoopm_live / megoopm_stream); error
    -- lines start with nginx's own timestamp.
    if body:sub(1, 1) == "{" then
        local kind = host:sub(1, 1) == "s" and "stream" or "request"
        local f = cjson.decode(body)
        if type(f) == "table" then
            return { host = host, log = kind, sev = sev, body = body, truncated = false, f = f }
        end
        return { host = host, log = kind, sev = sev, body = body, truncated = true }
    end

    local kind
    if body:find(DENIED, 1, true) then
        kind = "security"
    elseif sev <= WARN then
        kind = "error"
    else
        for _, needle in ipairs(KEEP_AT_INFO) do
            if body:find(needle, 1, true) then
                kind = "error"
                break
            end
        end
    end
    if not kind then
        return nil
    end
    return { host = host, log = kind, sev = sev, body = body, truncated = false }
end

local function message(p)
    return cjson.encode({
        node = conf.node,
        host = p.host,
        log = p.log,
        sev = p.sev,
        f = p.f or null,
        raw = p.f and null or p.body,
        truncated = p.truncated,
    })
end

local function connect()
    local ok_mod, redis = pcall(require, "resty.redis")
    if not ok_mod then
        return nil
    end
    local red = redis:new()
    red:set_timeouts(TIMEOUT_MS, TIMEOUT_MS, TIMEOUT_MS)
    local ok = red:connect(conf.redis_host, conf.redis_port)
    if not ok then
        return nil
    end
    return red
end

local function relay(premature, p, watchers)
    if premature then
        return
    end
    local shm = ngx.shared[DICT]

    local red = connect()
    if not red then
        -- Unreachable: drop, and do not try again for this host until the
        -- cache entry lapses -- a Redis outage must not cost a connect per line.
        if shm then
            shm:set(p.host, 0, NUMSUB_TTL)
        end
        return
    end

    local channel = CHANNEL .. p.host
    local healthy = true
    if watchers == nil then
        local res = red:pubsub("numsub", channel)
        healthy = type(res) == "table"
        watchers = healthy and tonumber(res[2]) or 0
        if shm then
            shm:set(p.host, watchers, NUMSUB_TTL)
        end
    end
    if watchers > 0 then
        healthy = red:publish(channel, message(p)) ~= nil
    end

    if healthy then
        red:set_keepalive(10000, 32)
    else
        red:close()
    end
end

-- One call per datagram, from the relay server's content_by_lua_block.
--
-- The Redis work runs in a timer, not here. nginx keeps a UDP session per
-- source address, and one nginx worker sends every line from one socket: a
-- datagram that arrives while this handler is still waiting on Redis lands in
-- the busy session and is lost (measured: 8 of 50 lines in a burst). Returning
-- at once closes the session before the next line arrives.
function M.handle()
    if not conf.enabled then
        return
    end
    local ok, err = pcall(function()
        local sock = ngx.req.socket()
        local p = M.parse(sock:receive())
        if not p then
            return
        end
        local shm = ngx.shared[DICT]
        local watchers = shm and shm:get(p.host)
        if watchers == 0 then
            -- Nobody watching, as of the last second: the common case costs
            -- nothing but the parse.
            return
        end
        -- A full timer queue drops the line, silently: it is a busy moment,
        -- and a log line per dropped line would make it busier.
        ngx.timer.at(0, function(premature)
            local ok_relay, relay_err = pcall(relay, premature, p, watchers)
            if not ok_relay then
                ngx.log(ngx.WARN, "[megoopm] live relay: ", tostring(relay_err))
            end
        end)
    end)
    if not ok then
        ngx.log(ngx.WARN, "[megoopm] live relay: ", tostring(err))
    end
end

return M
