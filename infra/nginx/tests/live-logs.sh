#!/usr/bin/env bash
# End-to-end check of the live log relay: nginx -> loopback UDP syslog -> the
# stream relay (Lua) -> Redis pub/sub. Offline: the nginx image, redis:7-alpine
# and curl on an internal network, no CrowdSec.
#
# Two nginx nodes share one Redis, like an HA pair. A probe server on each
# carries the live directives the generated hosts get (tag mgm_p1); a stream
# probe carries the stream form (tag mgm_s1). The working tree's nginx.conf and
# Lua are mounted over the image's copies, and the image is rebuilt first, so a
# run always tests what is on disk. Exit status is the verdict.
#
#   MSYS_NO_PATHCONV=1 bash infra/nginx/tests/live-logs.sh
set -euo pipefail

# `pwd -W` gives Docker Desktop a Windows path under Git Bash; plain pwd elsewhere.
REPO="$(cd "$(dirname "$0")/../../.." && { pwd -W 2>/dev/null || pwd; })"
NET=megoopm-live-probe
IMAGE=megoopm-nginx-probe
WORK="$(mktemp -d)"
# Docker Desktop cannot mount a Git Bash /tmp path; cygpath gives it the Windows one.
WORK_MOUNT="$(cygpath -m "$WORK" 2>/dev/null || echo "$WORK")"
CONTAINERS="ll-redis ll-nginx-a ll-nginx-b ll-nginx-c ll-cs ll-client ll-sub ll-sub2 ll-monitor"
FAILED=0

cleanup() {
  # shellcheck disable=SC2086
  docker rm -f -v $CONTAINERS >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
cleanup

docker build -q -t "$IMAGE" "$REPO/infra/nginx" >/dev/null
docker network create --internal "$NET" >/dev/null

expect() { # label expected actual
  if [ "$2" = "$3" ]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; FAILED=1; fi
}

# --- the probe hosts ----------------------------------------------------------
# Exactly the block the generated templates render (_live_log.j2); the
# megoopm_live format names $mgm_cs, so each server declares it.
mkdir -p "$WORK/nginx/conf.d/stream" "$WORK/nginx/default" "$WORK/empty"
cat > "$WORK/nginx/conf.d/probe.conf" <<'EOF'
server {
    listen 8001;
    # Room for the 10 KB URL below: the default 8k buffer would answer 414
    # before the request line reached the log.
    large_client_header_buffers 4 16k;
    set $mgm_cs "";
    include /etc/nginx/logging-targets.conf;
    access_log syslog:server=127.0.0.1:5140,tag=mgm_p1,nohostname megoopm_live;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_p1,nohostname info;

    location / { return 200 "ok\n"; }
    # Nothing listens on :9, so this is the "upstream refused" error line.
    location /up { proxy_pass http://127.0.0.1:9; }
}
# The two phases CrowdSec enforces in. A proxy host enforces in the access phase
# (which a location-level `return` would skip, hence the proxy_pass); a server
# that only answers with `return` enforces in the server-rewrite phase, before
# `set` runs, and so carries uninitialized_variable_warn off.
server {
    listen 8002;
    set $mgm_cs "";
    include /etc/nginx/logging-targets.conf;
    access_log syslog:server=127.0.0.1:5140,tag=mgm_p2,nohostname megoopm_live;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_p2,nohostname info;
    access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
    location / { proxy_pass http://127.0.0.1:8001; }
}
server {
    listen 8003;
    set $mgm_cs "";
    uninitialized_variable_warn off;
    include /etc/nginx/logging-targets.conf;
    access_log syslog:server=127.0.0.1:5140,tag=mgm_r3,nohostname megoopm_live;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_r3,nohostname info;
    server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
    location / { return 200 "ok\n"; }
}
EOF
cat > "$WORK/nginx/conf.d/stream/probe.conf" <<'EOF'
server {
    listen 9001;
    proxy_pass 127.0.0.1:8001;
    access_log syslog:server=127.0.0.1:5140,tag=mgm_s1,nohostname megoopm_stream;
}
EOF

# --- 0. A bare install (no hosts at all) passes `openresty -t` ------------------
# The base default server is the only one declaring $mgm_cs here.
OUT="$(MSYS_NO_PATHCONV=1 docker run --rm -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$WORK_MOUNT/empty:/data" "$IMAGE" \
  openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -t 2>&1)" && RC=0 || RC=$?
expect "bare install, openresty -t" 0 "$RC"
[ "$RC" = 0 ] || echo "$OUT" | tail -5

docker run -d --name ll-redis --network "$NET" redis:7-alpine >/dev/null

start_nginx() { # name node-id [extra docker args...]
  # File by file, not the directory: the entrypoint writes into /etc/nginx/lua.
  local lua=() f
  for f in "$REPO"/infra/nginx/lua/*.lua; do
    lua+=(-v "$f:/etc/nginx/lua/$(basename "$f"):ro")
  done
  MSYS_NO_PATHCONV=1 docker run -d --name "$1" --network "$NET" \
    -e NODE_ID="$2" -e REDIS_URL=redis://ll-redis:6379/0 "${@:3}" "${lua[@]}" \
    -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "$WORK_MOUNT:/data" "$IMAGE" >/dev/null
}
start_nginx ll-nginx-a node-a
start_nginx ll-nginx-b node-b
docker run -d --name ll-client --network "$NET" --entrypoint sleep curlimages/curl:8.11.1 600 >/dev/null
CLIENT_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" ll-client)"

curl_() { # curl args... ; a refused connection reports 000 rather than ending the run
  docker exec ll-client curl -s -o /dev/null -w '%{http_code}' "$@" || true
}
for node in a b; do
  for _ in $(seq 30); do
    [ "$(curl_ "http://ll-nginx-$node/healthz")" = 200 ] && break
    sleep 1
  done
done

numsub() { docker exec ll-redis redis-cli PUBSUB NUMSUB "megoopm:live:$1" | tail -1; }

# --- 1. Watched lines are published ---------------------------------------------
docker run -d --name ll-sub --network "$NET" redis:7-alpine \
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:p1 megoopm:live:s1 >/dev/null
for _ in $(seq 20); do [ "$(numsub p1)" = 1 ] && break; sleep 0.5; done

# The `\n` is a literal backslash and n: HTTP cannot carry a raw newline in a
# header, so this is what an attacker can actually send -- and it must stay a
# backslash through nginx's escaping and the relay's re-encoding.
UA='"<img src=x onerror=alert(1)>" with "quotes"\n'
BIG="/big?x=$(head -c 10240 /dev/zero | tr '\0' 'A')"
for node in a b; do
  for i in 1 2 3; do
    expect "request on node-$node" 200 "$(curl_ "http://ll-nginx-$node:8001/req-$node-$i?q=$i")"
  done
done
expect "hostile user agent" 200 "$(curl_ -A "$UA" "http://ll-nginx-a:8001/ua")"
expect "10 KB URL" 200 "$(curl_ "http://ll-nginx-a:8001$BIG")"
expect "closed upstream" 502 "$(curl_ "http://ll-nginx-b:8001/up")"
expect "stream connection" 200 "$(curl_ "http://ll-nginx-a:9001/via-stream")"
# 200 requests on one connection in a few milliseconds: none may be lost.
docker exec ll-client curl -s -o /dev/null "http://ll-nginx-a:8001/burst-[1-200]" || true
sleep 2

docker logs ll-sub > "$WORK/sub.out" 2>&1
# One tab-separated row per published message, flattened by the image's own
# cjson so the payload is checked as JSON, not grepped as text:
#   node host log truncated st m u ip ua raw-prefix cs   (or INVALID)
cat > "$WORK/summarize.lua" <<'EOF'
local cjson = require("cjson.safe")
local lines = {}
for l in io.lines(arg[1]) do lines[#lines + 1] = l end
local function s(v) return type(v) == "string" and v or (v == nil and "" or tostring(v)) end
local i = 1
while i <= #lines do
    if lines[i] == "message" and lines[i + 2] then
        local m = cjson.decode(lines[i + 2])
        if type(m) ~= "table" then
            print("INVALID\t" .. lines[i + 2]:sub(1, 80))
        else
            local f = type(m.f) == "table" and m.f or {}
            local raw = type(m.raw) == "string" and m.raw:sub(1, 120) or ""
            print(table.concat({ s(m.node), s(m.host), s(m.log), s(m.truncated), s(f.st),
                s(f.m), s(f.u), s(f.ip), s(f.ua), raw, s(f.cs) }, "\t"))
        end
        i = i + 3
    else
        i = i + 1
    end
end
EOF
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" "$IMAGE" \
  /w/summarize.lua /w/sub.out > "$WORK/sum.tsv"

rows() { # awk condition over the summary columns ($1 node .. $10 raw)
  awk -F'\t' "$1" "$WORK/sum.tsv" | wc -l | tr -d ' '
}
expect "every message is valid JSON" 0 "$(rows '$1 == "INVALID"')"
for node in a b; do
  for i in 1 2 3; do
    expect "one request line for /req-$node-$i, from node-$node, with st/m/u/ip" 1 \
      "$(rows "\$1 == \"node-$node\" && \$2 == \"p1\" && \$3 == \"request\" && \$5 == \"200\" \
               && \$6 == \"GET\" && \$7 == \"/req-$node-$i?q=$i\" && \$8 == \"$CLIENT_IP\"")"
  done
done
expect "no request line is duplicated" 0 "$(awk -F'\t' '$3 == "request" && $7 != "" {print $1 $7}' \
  "$WORK/sum.tsv" | sort | uniq -d | wc -l | tr -d ' ')"
expect "the hostile user agent arrives intact" 1 \
  "$(UA="$UA" awk -F'\t' '$7 == "/ua" && $9 == ENVIRON["UA"]' "$WORK/sum.tsv" | wc -l | tr -d ' ')"
expect "the 10 KB URL arrives raw, marked truncated" 1 \
  "$(rows '$1 == "node-a" && $3 == "request" && $4 == "true" && $10 ~ /^\{.*\/big\?x=AAAA/')"
expect "the closed upstream yields an error line" 1 \
  "$(rows '$1 == "node-b" && $3 == "error" && $10 ~ /connect\(\) failed/')"
expect "the closed upstream's request line says 502" 1 "$(rows '$1 == "node-b" && $5 == "502" && $7 == "/up"')"
expect "the stream connection yields a stream line" 1 \
  "$(rows '$1 == "node-a" && $2 == "s1" && $3 == "stream" && $5 == "200" && $8 != ""')"
expect "all 200 burst requests arrive" 200 "$(rows '$3 == "request" && $7 ~ /^\/burst-/')"
expect "info-level noise is dropped" 0 "$(rows '$3 == "error" && $10 ~ /\[info\]/ && $10 !~ /SSL/')"

# --- 2. Parsing, case by case (the pure M.parse) --------------------------------
cat > "$WORK/parse.lua" <<'EOF'
package.path = "/lua/?.lua;" .. package.path
local relay = require("megoopm_live_relay")
local failed = false
local function check(label, datagram, want)
    local got = relay.parse(datagram)
    local ok
    if want == nil then
        ok = got == nil
    else
        ok = got ~= nil
        for k, v in pairs(want) do
            ok = ok and got[k] == v
        end
    end
    print((ok and "ok    " or "FAIL  ") .. "parse: " .. label)
    if not ok then failed = true end
end
local H = "<190>Oct 25 10:00:00 "
check("a request line", H .. 'mgm_p1: {"st":"200"}',
    { host = "p1", log = "request", sev = 6, truncated = false })
check("a single-digit day", '<190>Oct  5 10:00:00 mgm_r22: {"st":"301"}', { host = "r22", log = "request" })
check("a cut-off request line", H .. 'mgm_p1: {"st":"200","u":"/aaa',
    { host = "p1", log = "request", truncated = true, body = '{"st":"200","u":"/aaa' })
check("a stream line", H .. 'mgm_s4: {"st":"200"}', { host = "s4", log = "stream" })
check("an error", "<187>Oct 25 10:00:00 mgm_p1: 2026/10/25 10:00:00 [error] 7#7: *1 connect() failed",
    { host = "p1", log = "error", sev = 3, truncated = false })
check("a warning", "<188>Oct 25 10:00:00 mgm_d3: 2026/10/25 10:00:00 [warn] 7#7: *1 an upstream response is buffered",
    { log = "error", sev = 4 })
-- The request context nginx appends; the referrer is the attacker's to write.
local function ctx(referrer)
    return ', client: 1.2.3.4, server: _, request: "GET / HTTP/1.1", host: "a.example"'
        .. (referrer and (', referrer: "' .. referrer .. '"') or "")
end
check("a TLS handshake failure at info", H .. "mgm_p1: 2026/10/25 10:00:00 [info] 7#7: *1 SSL_do_handshake() failed"
    .. " (SSL: error:0A00006C:SSL routines::bad key share) while SSL handshaking, client: 1.2.3.4, server: 0.0.0.0:443",
    { log = "error", sev = 6 })
check("a peer closing the TLS handshake", H .. "mgm_u: 2026/10/25 10:00:00 [info] 7#7: *1 peer closed connection"
    .. " in SSL handshake while SSL handshaking, client: 1.2.3.4, server: 0.0.0.0:443",
    { host = "u", log = "error" })
-- Verbatim shape of the bouncer's line (seen in this harness's own logs).
check("a CrowdSec denial", "<185>Oct 25 10:00:00 mgm_u: 2026/10/25 10:00:00 [alert] 23#23: *127 [lua] crowdsec.lua:902:"
    .. " Allow(): [Crowdsec] denied '1.2.3.4' with 'ban' (by appsec)" .. ctx(),
    { host = "u", log = "security", sev = 1 })
check("a referrer saying [Crowdsec] denied is still just an error",
    "<187>Oct 25 10:00:00 mgm_p1: 2026/10/25 10:00:00 [error] 7#7: *1 open() \"/var/empty/megoopm/x\" failed"
    .. " (2: No such file or directory)" .. ctx("[Crowdsec] denied '9.9.9.9' with 'ban' (by appsec)"),
    { log = "error", sev = 3 })
check("a URI saying [Crowdsec] denied is still just an error",
    "<187>Oct 25 10:00:00 mgm_p1: 2026/10/25 10:00:00 [error] 7#7: *1 open() \"/var/empty/megoopm/[Crowdsec] denied\""
    .. " failed (2: No such file or directory)" .. ctx(),
    { log = "error" })
check("a referrer saying SSL_do_handshake() failed is still dropped at info",
    H .. "mgm_p1: 2026/10/25 10:00:00 [info] 7#7: *1 client prematurely closed connection while sending response"
    .. ctx("SSL_do_handshake() failed"), nil)
check("a header saying SSL_do_handshake() failed is still dropped at info",
    H .. "mgm_p1: 2026/10/25 10:00:00 [info] 7#7: *1 client sent invalid header line: \"X: SSL_do_handshake() failed\""
    .. " while reading client request headers" .. ctx(), nil)
check("info noise is dropped", H .. "mgm_p1: 2026/10/25 10:00:00 [info] 7#7: *1 client closed connection while waiting for request", nil)
check("notice noise is dropped", "<189>Oct 25 10:00:00 mgm_p1: 2026/10/25 10:00:00 [notice] 7#7: signal process started", nil)
check("a foreign tag is dropped", H .. 'nginx: {"st":"200"}', nil)
check("garbage is dropped", "hello", nil)
check("no datagram at all is dropped", nil, nil)
os.exit(failed and 1 or 0)
EOF
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" \
  -v "$REPO/infra/nginx/lua:/lua:ro" "$IMAGE" /w/parse.lua > "$WORK/parse.out" 2>&1 || true
cat "$WORK/parse.out"
grep -q '^FAIL' "$WORK/parse.out" && FAILED=1
[ "$(grep -c '^ok' "$WORK/parse.out" || true)" = 18 ] || { echo "FAIL  parse: not every case ran"; FAILED=1; }

# --- 2b. A CrowdSec denial: a security line, and a request line marked denied ---
# A LAPI-only CrowdSec (no agent, no online API, so it installs no hub items and
# works on this network) holds one ban decision for the client. Node c enforces
# against it; its bouncer refreshes every 10 s (UPDATE_FREQUENCY).
docker run -d --name ll-cs --network "$NET" -e DISABLE_AGENT=true -e DISABLE_ONLINE_API=true \
  -e CROWDSEC_BYPASS_DB_VOLUME_CHECK=true -e BOUNCER_KEY_megoopm=livelogs-probe-key crowdsecurity/crowdsec:v1.8.1 >/dev/null
for _ in $(seq 90); do
  docker exec ll-cs cscli bouncers list 2>/dev/null | grep -q megoopm && break
  sleep 1
done
# AppSec off for real: the entrypoint turns an empty CROWDSEC_APPSEC_URL back
# into crowdsec:7422, so the bouncer template itself gets a blank APPSEC_URL.
# Otherwise an unreachable AppSec fails closed and denies every request.
sed 's/^APPSEC_URL=.*/APPSEC_URL=/' "$REPO/infra/nginx/crowdsec-bouncer.conf" > "$WORK/bouncer-noappsec.conf"
start_nginx ll-nginx-c node-c -e CROWDSEC_LAPI_URL=http://ll-cs:8080 -e CROWDSEC_BOUNCER_KEY=livelogs-probe-key \
  -v "$WORK_MOUNT/bouncer-noappsec.conf:/etc/nginx/crowdsec-bouncer.conf.template:ro"
for _ in $(seq 30); do [ "$(curl_ "http://ll-nginx-c/healthz")" = 200 ] && break; sleep 1; done
docker run -d --name ll-sub2 --network "$NET" redis:7-alpine \
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:p2 megoopm:live:r3 >/dev/null
for _ in $(seq 20); do [ "$(numsub p2)" = 1 ] && [ "$(numsub r3)" = 1 ] && break; sleep 0.5; done
# Before the ban: the bouncer lets the client through both phases. Allow() ends
# an allowed request with ngx.exit(ngx.DECLINED), so the request line must not
# keep the mark set before it.
expect "the client passes the access phase before the ban" 200 "$(curl_ "http://ll-nginx-c:8002/allowed-access")"
expect "the client passes the server-rewrite phase before the ban" 200 "$(curl_ "http://ll-nginx-c:8003/allowed-rewrite")"
docker exec ll-cs cscli decisions add -i "$CLIENT_IP" -d 1h >/dev/null
# Wait for the bouncer to pick the decision up (polled on the default server,
# so the probes below are the only denials the two probe servers see). AppSec is
# off on this node: only the ban decision is under test.
for _ in $(seq 40); do
  [ "$(curl_ "http://ll-nginx-c/warmup")" = 403 ] && break
  sleep 1
done
expect "the banned client is refused in the access phase" 403 "$(curl_ "http://ll-nginx-c:8002/denied-access")"
expect "the banned client is refused in the server-rewrite phase" 403 "$(curl_ "http://ll-nginx-c:8003/denied-rewrite")"
sleep 2
docker logs ll-sub2 > "$WORK/sub2.out" 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" "$IMAGE" \
  /w/summarize.lua /w/sub2.out > "$WORK/sum2.tsv"
rows2() { awk -F'\t' "$1" "$WORK/sum2.tsv" | wc -l | tr -d ' '; }
for u in denied-access:p2 denied-rewrite:r3; do
  expect "${u%%:*}: the request line carries cs=denied" 1 \
    "$(rows2 "\$2 == \"${u##*:}\" && \$3 == \"request\" && \$5 == \"403\" && \$7 == \"/${u%%:*}\" && \$11 == \"denied\"")"
  expect "${u%%:*}: the denial is also a security line" 1 \
    "$(rows2 "\$2 == \"${u##*:}\" && \$3 == \"security\" && \$10 ~ /denied '$CLIENT_IP'/")"
done
for u in allowed-access:p2 allowed-rewrite:r3; do
  expect "${u%%:*}: an allowed request line carries cs empty" 1 \
    "$(rows2 "\$2 == \"${u##*:}\" && \$3 == \"request\" && \$5 == \"200\" && \$7 == \"/${u%%:*}\" && \$11 == \"\"")"
done
expect "only denied requests are marked denied" 0 "$(rows2 '$3 == "request" && $11 == "denied" && $5 != "403"')"
expect "the server-rewrite bounce logs no unset-variable warning" 0 \
  "$(docker logs ll-nginx-c 2>&1 | grep -c 'uninitialized "mgm_cs"' || true)"
# Nodes a and b have no bouncer: their request lines never carry a mark.
expect "requests that were never denied carry cs empty" 0 "$(rows '$3 == "request" && $11 != ""')"

# --- 3. Nobody watching: the relay asks, and publishes nothing ------------------
docker rm -f ll-sub >/dev/null
for _ in $(seq 20); do [ "$(numsub p1)" = 0 ] && break; sleep 0.5; done
sleep 2 # past the 1 s NUMSUB cache
docker run -d --name ll-monitor --network "$NET" redis:7-alpine redis-cli -h ll-redis MONITOR >/dev/null
sleep 1
for node in a b; do
  curl_ "http://ll-nginx-$node:8001/unwatched" >/dev/null
  curl_ "http://ll-nginx-$node/scanner-probe" >/dev/null # the base default server, tag mgm_u
done
sleep 2
docker logs ll-monitor > "$WORK/monitor.out" 2>&1
expect "unwatched: no PUBLISH" 0 "$(grep -ci '"publish"' "$WORK/monitor.out" || true)"
expect "unwatched: p1 is asked about" yes \
  "$(grep -qi '"pubsub" "numsub" "megoopm:live:p1"' "$WORK/monitor.out" && echo yes || echo no)"
expect "unwatched: the default server (u) is asked about" yes \
  "$(grep -qi '"pubsub" "numsub" "megoopm:live:u"' "$WORK/monitor.out" && echo yes || echo no)"

# The default server's bouncer denies these probes (no CrowdSec here: AppSec
# fails closed) before `set $mgm_cs` runs; that must not warn on every hit.
expect "a denied default-server request logs no warning" 0 \
  "$(docker logs ll-nginx-a 2>&1 | grep -c 'uninitialized "mgm_cs"' || true)"

# --- 4. Redis down: nothing fails, nothing blocks (Review Focus 1) --------------
docker rm -f ll-monitor ll-redis >/dev/null
for node in a b; do
  for i in 1 2 3; do
    expect "redis down, request on node-$node" 200 "$(curl_ "http://ll-nginx-$node:8001/down-$i")"
  done
  T="$(docker exec ll-client curl -s -o /dev/null -w '%{time_total}' "http://ll-nginx-$node:8001/timed" || echo 9)"
  expect "redis down, node-$node answers within 0.5 s" yes "$(awk -v t="$T" 'BEGIN { print (t < 0.5) ? "yes" : "no" }')"
  docker exec "ll-nginx-$node" openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -t \
    >/dev/null 2>&1 && RC=0 || RC=$?
  expect "redis down, openresty -t on node-$node" 0 "$RC"
  docker exec "ll-nginx-$node" openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -s reload \
    >/dev/null 2>&1 && RC=0 || RC=$?
  expect "redis down, reload on node-$node" 0 "$RC"
  sleep 1
  expect "redis down, request after reload on node-$node" 200 "$(curl_ "http://ll-nginx-$node:8001/after-reload")"
done

# The relay logs a fault to stderr (never to itself); there must be none, even
# with Redis gone -- an unreachable Redis is a dropped line, not a fault.
expect "the relay logged no fault" 0 \
  "$( (docker logs ll-nginx-a 2>&1; docker logs ll-nginx-b 2>&1) | grep -c 'live relay' || true)"

[ "$FAILED" = 0 ] && echo "PASS" || { echo "FAILED"; docker logs ll-nginx-a 2>&1 | tail -30; }
exit "$FAILED"
