-- Live logs relay: nginx's live log lines in, Redis pub/sub out, nothing kept.
--
-- Every server block sends its request and error lines, as syslog over UDP,
-- to 127.0.0.1:5140 -- the stream server in nginx.conf that runs this module,
-- once per datagram. Each line is classified, tagged with this node's id and
-- PUBLISHed to `megoopm:live:<host key>` while that channel has a subscriber
-- (a Live logs page watching that host), and to `megoopm:live:all` while that
-- one has (a page watching All logs); with neither, it is dropped on arrival.
-- nginx's own messages -- its main error log, and error lines no host key is
-- attached to -- are host `n`. Redis is a pass-through here: no key is ever
-- written.
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
-- Lines per second per host on this node. A busy watched host must not load
-- nginx, Redis and the backend with lines the page would skip anyway (a page
-- shows at most 200 a second). Over the cap, lines are dropped here and
-- counted, and the count is published once a second as
-- {"log":"skipped",…,"count":N}, so skipping is never silent.
local CAP = 300
-- All logs: every host's lines, and nginx's own, on one more channel. It has a
-- NUMSUB count, a cap of 300 a second and skipped reports of its own, kept
-- like a host's under the key `all` -- which is why no line may be for a host
-- of that name.
local ALL = "all"
-- nginx itself: the main error log (tag mgm_n) and error lines that carry no
-- host key (the shared tag mgm_x).
local NGINX = "n"
-- The keys of this relay's counters in the shared dict, next to the NUMSUB
-- counts (which are keyed by the bare host key; a host key has no ":").
local function lines_key(host)
    return "n:" .. host .. ":" .. math.floor(ngx.now())
end
local function skipped_key(host)
    return "sk:" .. host
end
local function report_key(host)
    return "skt:" .. host
end

-- nginx's syslog header without the hostname (`nohostname`): PRI, the BSD
-- timestamp (day padded with a space), then the tag `mgm_<host key>`.
local HEADER = "^<(%d+)>%a+ +%d+ [%d:]+ mgm_([%w]+): (.*)$"
-- Access lines all arrive through one access log per nginx context, tagged
-- `mgm_x`, and name their host themselves, first: {"k":"p3","ts":…}. nginx
-- writes that value from the server block's own `set $mgm_key`, ahead of
-- every value a client controls, so the pattern cannot be spoofed. Error
-- lines carry no variables and keep the key in their tag.
local ACCESS_TAG = "x"
local ACCESS_KEY = '^{"k":"(%w*)",'
-- Error lines at warn and above are kept (syslog severity 0..4).
local WARN = 4
-- nginx's own lines down to notice (5): its process messages -- reloads,
-- workers starting and exiting -- are notices.
local NOTICE = 5
-- nginx's own messages the relay itself causes, on the main log: relayed as n
-- lines, each would need one more timer -- the very thing that ran out. Matched
-- at the start of nginx's message ("stream lua: " is the relay's VM, the bare
-- count http's).
local FED_BACK = {
    "^stream lua: %d+ lua_max_running_timers are not enough",
    "^%d+ lua_max_running_timers are not enough",
}

-- CLASSIFYING AN ERROR LINE: ONLY ON WHAT NGINX WROTE
--
-- An error line is `<date> <time> [level] pid#tid: *conn <message>`, then the
-- request context nginx appends: `, client: …, server: …, request: "…",
-- host: "…", referrer: "…"`. The context is attacker-controlled (a Referer of
-- "[Crowdsec] denied" must not make a 404 a security event), and so are parts
-- of some messages (`open() "/var/empty/<uri>" failed`, `client sent invalid
-- header line: "<header>"`). So the markers are matched at the START of the
-- message, where only nginx or the bouncer's own ngx.log prefix can put them,
-- and the context after the first ", client: " is never searched. The
-- published `raw` is still the whole line.
local PREFIX = "^%d+/%d+/%d+ [%d:]+ %[%a+%] %d+#%d+: "
local CONN = "^%*%d+ "
-- nginx logs these TLS failures at info, but an admin chasing a handshake
-- problem needs them. Each is how nginx's message begins.
local KEEP_AT_INFO = {
    "SSL_do_handshake() failed",
    "peer closed connection in SSL handshake",
}
-- The bouncer's denial, as ngx.log writes it:
--   [lua] crowdsec.lua:902: Allow(): [Crowdsec] denied '1.2.3.4' with 'ban' (by appsec)
-- The marker must be the first "[" after the Lua source location.
local DENIED = "^%[lua%] %S+:%d+: [^%[]*%[Crowdsec%] denied"

-- nginx's own message: without the timestamp/level/pid prefix and without the
-- request context.
local function nginx_message(body)
    local msg = body
    local _, prefix_end = msg:find(PREFIX)
    if prefix_end then
        msg = msg:sub(prefix_end + 1)
        local _, conn_end = msg:find(CONN)
        if conn_end then
            msg = msg:sub(conn_end + 1)
        end
    end
    local context = msg:find(", client: ", 1, true)
    if context then
        msg = msg:sub(1, context - 1)
    end
    return msg
end

-- Pure and cheap: which host a datagram is for, without decoding it, so a
-- line nobody watches costs no more than this. nil for anything not ours,
-- and for a line with an empty key (a server that is not a host).
--   { host, sev, body, access }
function M.split(datagram)
    if type(datagram) ~= "string" then
        return nil
    end
    local pri, tag, body = datagram:match(HEADER)
    if not pri then
        return nil
    end
    -- Access lines are JSON objects (megoopm_live / megoopm_stream); error
    -- lines start with nginx's own timestamp.
    local access = body:sub(1, 1) == "{"
    local host = tag
    if tag == ACCESS_TAG then
        -- A tag of its own (not `x`) is a block rendered before the access log
        -- was shared; it still names the host, until the worker re-renders.
        -- An error line under the shared tag names no host: it is nginx's own.
        if access then
            host = body:match(ACCESS_KEY)
        else
            host = NGINX
        end
    end
    if not host or host == "" or host == ALL then
        return nil
    end
    return { host = host, sev = tonumber(pri) % 8, body = body, access = access }
end

-- Pure: no I/O, no ngx state. Takes M.split's result; returns nil for
-- anything that is not shown.
--   { host, log, sev, body, truncated, f }
-- `f` is the decoded request/stream line; absent when the body is not JSON.
-- A body that starts as JSON but does not parse was cut short by nginx's
-- syslog line limit (a long URL): it is kept, raw, as `truncated`.
function M.classify(s)
    local host, sev, body = s.host, s.sev, s.body
    if s.access then
        local kind = host:sub(1, 1) == "s" and "stream" or "request"
        local f = cjson.decode(body)
        if type(f) == "table" then
            f.k = nil -- the key travels as `host`
            return { host = host, log = kind, sev = sev, body = body, truncated = false, f = f }
        end
        return { host = host, log = kind, sev = sev, body = body, truncated = true }
    end

    if host == NGINX then
        -- nginx's own: no request, so no denial and no TLS handshake to keep.
        if sev > NOTICE then
            return nil
        end
        local own = nginx_message(body)
        for _, needle in ipairs(FED_BACK) do
            if own:find(needle) then
                return nil
            end
        end
        return { host = host, log = "error", sev = sev, body = body, truncated = false }
    end

    local msg = nginx_message(body)
    local kind
    if msg:find(DENIED) then
        kind = "security"
    elseif sev <= WARN then
        kind = "error"
    else
        for _, needle in ipairs(KEEP_AT_INFO) do
            if msg:sub(1, #needle) == needle then
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

-- Both steps, for a datagram in hand.
function M.parse(datagram)
    local s = M.split(datagram)
    return s and M.classify(s)
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

-- Publishes `p` on each of `targets`: { { key = <host key or "all">, watchers =
-- <cached count, or nil when not known> }, ... }. The same message on each.
local function relay(premature, p, targets)
    if premature then
        return
    end
    local shm = ngx.shared[DICT]

    local red = connect()
    if not red then
        -- Unreachable: drop, and do not try again for these channels until
        -- the cache entries lapse -- a Redis outage must not cost a connect
        -- per line.
        if shm then
            for _, t in ipairs(targets) do
                shm:set(t.key, 0, NUMSUB_TTL)
            end
        end
        return
    end

    local healthy = true
    local msg
    for _, t in ipairs(targets) do
        local channel = CHANNEL .. t.key
        local watchers = t.watchers
        if watchers == nil then
            local res = red:pubsub("numsub", channel)
            healthy = type(res) == "table"
            watchers = healthy and tonumber(res[2]) or 0
            if shm then
                shm:set(t.key, watchers, NUMSUB_TTL)
            end
        end
        if healthy and watchers > 0 then
            msg = msg or message(p)
            healthy = red:publish(channel, msg) ~= nil
        end
        if not healthy then
            break
        end
    end

    if healthy then
        red:set_keepalive(10000, 32)
    else
        red:close()
    end
end

-- A second after a host's first skipped line: publish how many were skipped.
local function report_skipped(premature, host)
    if premature then
        return
    end
    local shm = ngx.shared[DICT]
    if not shm then
        return
    end
    -- The flag goes first: a line skipped from here on schedules the next
    -- report, so nothing counted is left without one.
    shm:delete(report_key(host))
    local n = shm:get(skipped_key(host)) or 0
    if n <= 0 then
        return
    end
    shm:incr(skipped_key(host), -n)
    local red = connect()
    if not red then
        return
    end
    -- By hand, so "log" comes first: the backend tells this message from a
    -- line by that prefix, without decoding every line.
    local msg = string.format('{"log":"skipped","node":%s,"host":%s,"count":%d}',
        cjson.encode(conf.node), cjson.encode(host), n)
    if red:publish(CHANNEL .. host, msg) ~= nil then
        red:set_keepalive(10000, 32)
    else
        red:close()
    end
end

-- Count one line this relay did not publish for `host`, and make sure a
-- report is on its way.
local function skip(shm, host)
    if not shm then
        return
    end
    shm:incr(skipped_key(host), 1, 0)
    -- add() succeeds for the first skip only; the flag expires on its own, so
    -- a report timer that never ran cannot stop the next one.
    if shm:add(report_key(host), true, 5) then
        local ok = ngx.timer.at(1, function(premature)
            local ok_report, report_err = pcall(report_skipped, premature, host)
            if not ok_report then
                ngx.log(ngx.WARN, "[megoopm] live relay: ", tostring(report_err))
            end
        end)
        if not ok then
            shm:delete(report_key(host))
        end
    end
end

-- Whether `host` is still under this second's cap; counts the line.
local function admit(shm, host)
    if not shm then
        return true
    end
    -- A full dict cannot count: let the line through rather than drop it.
    local n = shm:incr(lines_key(host), 1, 0, 2)
    return n == nil or n <= CAP
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
        local s = M.split(sock:receive())
        if not s then
            return
        end
        local shm = ngx.shared[DICT]
        -- nil: not known this second, so the timer asks.
        local watchers = shm and shm:get(s.host)
        local all_watchers = shm and shm:get(ALL)
        if watchers == 0 and all_watchers == 0 then
            -- Nobody watching the host or All logs, as of the last second:
            -- the common case costs one pattern match, and no JSON decode.
            return
        end
        -- An error line is filtered first: most are info noise, which must
        -- not use up the cap. An access line is always shown, so it is
        -- counted before it is decoded.
        local p
        if not s.access then
            p = M.classify(s)
            if not p then
                return
            end
        end
        -- Each channel has its own cap, and its own skipped count.
        local targets = {}
        local function target(key, n)
            if n == 0 then
                return
            end
            if admit(shm, key) then
                targets[#targets + 1] = { key = key, watchers = n }
            else
                skip(shm, key)
            end
        end
        target(s.host, watchers)
        target(ALL, all_watchers)
        if #targets == 0 then
            return
        end
        p = p or M.classify(s)
        -- A full timer queue cannot take the line: it is counted as skipped,
        -- like a line over the cap, rather than logged (a log line per lost
        -- line would add load at the worst moment).
        local scheduled = ngx.timer.at(0, function(premature)
            local ok_relay, relay_err = pcall(relay, premature, p, targets)
            if not ok_relay then
                ngx.log(ngx.WARN, "[megoopm] live relay: ", tostring(relay_err))
            end
        end)
        if not scheduled then
            for _, t in ipairs(targets) do
                skip(shm, t.key)
            end
        end
    end)
    if not ok then
        ngx.log(ngx.WARN, "[megoopm] live relay: ", tostring(err))
    end
end

return M
