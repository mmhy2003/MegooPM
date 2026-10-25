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
CONTAINERS="ll-redis ll-nginx-a ll-nginx-b ll-nginx-c ll-nginx-fd ll-cs ll-client ll-sub ll-sub2 ll-sub3 ll-sub-n
  ll-sub-all ll-sub-all2 ll-monitor"
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
# Exactly the block the generated templates render (_live_log.j2): the key in
# $mgm_key for the shared http-level access log, and an error log of its own.
# The megoopm_live format names $mgm_cs, so each server declares it.
mkdir -p "$WORK/nginx/conf.d/stream" "$WORK/nginx/default" "$WORK/empty"
cat > "$WORK/nginx/conf.d/probe.conf" <<'EOF'
server {
    listen 8001;
    # Room for the 10 KB URL below: the default 8k buffer would answer 414
    # before the request line reached the log.
    large_client_header_buffers 4 16k;
    set $mgm_key p1;
    set $mgm_cs "";
    include /etc/nginx/logging-error-targets.conf;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_p1,nohostname info;

    location / { return 200 "ok\n"; }
    # Nothing listens on :9, so this is the "upstream refused" error line.
    location /up { proxy_pass http://127.0.0.1:9; }
}
# The two phases CrowdSec enforces in. A proxy host enforces in the access phase
# (which a location-level `return` would skip, hence the proxy_pass); a server
# that only answers with `return` enforces in the server-rewrite phase, before
# `set` runs, so its hook names the key itself and the block carries
# uninitialized_variable_warn off.
server {
    listen 8002;
    set $mgm_key p2;
    set $mgm_cs "";
    include /etc/nginx/logging-error-targets.conf;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_p2,nohostname info;
    access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
    location / { proxy_pass http://127.0.0.1:8001; }
}
server {
    listen 8003;
    set $mgm_key r3;
    set $mgm_cs "";
    uninitialized_variable_warn off;
    include /etc/nginx/logging-error-targets.conf;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_r3,nohostname info;
    server_rewrite_by_lua_block { require("megoopm_crowdsec_check").check("r3") }
    location / { return 200 "ok\n"; }
}
# A block as this feature first rendered it: its own access log and tag, and the
# combined targets file. A new nginx must still run it, and still name its host,
# until the worker renders it again.
server {
    listen 8004;
    set $mgm_cs "";
    include /etc/nginx/logging-targets.conf;
    access_log syslog:server=127.0.0.1:5140,tag=mgm_p4,nohostname megoopm_live;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_p4,nohostname info;
    location / { return 200 "ok\n"; }
}
EOF
cat > "$WORK/nginx/conf.d/stream/probe.conf" <<'EOF'
server {
    listen 9001;
    proxy_pass 127.0.0.1:8001;
    set $mgm_key s1;
    error_log /usr/local/openresty/nginx/logs/error.log warn;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_s1,nohostname info;
}
EOF

# --- 0. A bare install (no hosts at all) passes `openresty -t` ------------------
# The base default server is the only one declaring $mgm_cs here.
OUT="$(MSYS_NO_PATHCONV=1 docker run --rm -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$WORK_MOUNT/empty:/data" "$IMAGE" \
  openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -t 2>&1)" && RC=0 || RC=$?
expect "bare install, openresty -t" 0 "$RC"
[ "$RC" = 0 ] || echo "$OUT" | tail -5

# --- 0b. A Redis URL the relay cannot use is said out loud at startup ----------
# The relay, like visitor analytics, connects without a password or TLS: with
# either in REDIS_URL, Live logs would show nothing from nginx, and quietly.
redis_warning() { # REDIS_URL -> yes/no
  MSYS_NO_PATHCONV=1 docker run --rm -e REDIS_URL="$1" "$IMAGE" true 2>&1 \
    | grep -q 'WARNING: REDIS_URL' && echo yes || echo no
}
expect "a warning for a password in REDIS_URL" yes "$(redis_warning 'redis://:secret@ll-redis:6379/0')"
expect "a warning for a user and password in REDIS_URL" yes "$(redis_warning 'redis://u:secret@ll-redis:6379/0')"
expect "a warning for rediss://" yes "$(redis_warning 'rediss://ll-redis:6380/0')"
expect "no warning for a plain REDIS_URL" no "$(redis_warning 'redis://ll-redis:6379/0')"
expect "the warning does not repeat the password" no \
  "$(MSYS_NO_PATHCONV=1 docker run --rm -e REDIS_URL='redis://:secret@ll-redis:6379/0' "$IMAGE" true 2>&1 \
     | grep -q secret && echo yes || echo no)"

docker run -d --name ll-redis --network "$NET" redis:7-alpine >/dev/null

start_nginx() { # name node-id [extra docker args...]; DATA / CONF override /data and nginx.conf
  # File by file, not the directory: the entrypoint writes into /etc/nginx/lua.
  local lua=() f
  for f in "$REPO"/infra/nginx/lua/*.lua; do
    lua+=(-v "$f:/etc/nginx/lua/$(basename "$f"):ro")
  done
  MSYS_NO_PATHCONV=1 docker run -d --name "$1" --network "$NET" \
    -e NODE_ID="$2" -e REDIS_URL=redis://ll-redis:6379/0 "${@:3}" "${lua[@]}" \
    -v "${CONF:-$REPO/infra/nginx/nginx.conf}:/etc/nginx/nginx.conf:ro" \
    -v "${DATA:-$WORK_MOUNT}:/data" "$IMAGE" >/dev/null
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
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:p1 megoopm:live:s1 megoopm:live:p4 >/dev/null
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
expect "a block in the earlier shape" 200 "$(curl_ "http://ll-nginx-a:8004/old-shape")"
# Refused before any `set` runs: no key of its own, and it must not warn (§3).
expect "a malformed request line" 400 "$(curl_ -X 'BAD METHOD' "http://ll-nginx-a:8001/")"
# 200 requests on one connection in a few milliseconds: none may be lost.
docker exec ll-client curl -s -o /dev/null "http://ll-nginx-a:8001/burst-[1-200]" || true
sleep 2

docker logs ll-sub > "$WORK/sub.out" 2>&1
# One tab-separated row per published message, flattened by the image's own
# cjson so the payload is checked as JSON, not grepped as text:
#   node host log truncated st m u ip ua raw-prefix cs count sev   (or INVALID)
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
                s(f.m), s(f.u), s(f.ip), s(f.ua), raw, s(f.cs), s(m.count), s(m.sev) }, "\t"))
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
expect "a block in the earlier shape still names its host" 1 \
  "$(rows '$2 == "p4" && $3 == "request" && $7 == "/old-shape"')"
expect "info-level noise is dropped" 0 "$(rows '$3 == "error" && $10 ~ /\[info\]/ && $10 !~ /SSL/')"

# --- 1b. Sockets per worker, with many hosts -------------------------------------
# Every syslog target an access_log/error_log directive names is its own peer,
# with its own socket in each worker, opened on first use and not counted in
# worker_connections. 150 server blocks in the generated shape, a CrowdSec
# syslog target configured (pointed at the relay, which drops its lines), one
# worker, and one request per block that writes an access line and an error
# line: what each extra block costs that worker is the number that matters.
FD_BLOCKS=150
mkdir -p "$WORK/fd/nginx/conf.d/stream" "$WORK/fd/nginx/default"
for i in $(seq "$FD_BLOCKS"); do
  key="d$((1000 + i))"
  cat <<EOF
server {
    listen 8100;
    server_name h$i.fd.test;
    root /var/empty/megoopm;
    set \$mgm_key $key;
    set \$mgm_cs "";
    include /etc/nginx/logging-error-targets.conf;
    error_log syslog:server=127.0.0.1:5140,tag=mgm_$key,nohostname info;
}
EOF
done > "$WORK/fd/nginx/conf.d/hosts.conf"
sed 's/^worker_processes .*/worker_processes 1;/' "$REPO/infra/nginx/nginx.conf" > "$WORK/nginx-1worker.conf"
CONF="$WORK_MOUNT/nginx-1worker.conf" DATA="$WORK_MOUNT/fd" \
  start_nginx ll-nginx-fd node-fd -e CROWDSEC_SYSLOG_ADDR=127.0.0.1:5140
for _ in $(seq 30); do [ "$(curl_ "http://ll-nginx-fd/healthz")" = 200 ] && break; sleep 1; done
worker_fds() { docker exec ll-nginx-fd sh -c 'ls /proc/$(pgrep -f "nginx: worker" | head -1)/fd | wc -l' | tr -d ' '; }
FD_BEFORE="$(worker_fds)"
# A missing file: a 404 access line plus an [error] open() line, in every block.
docker exec ll-client sh -c "for i in \$(seq $FD_BLOCKS); do
  curl -s -o /dev/null -H \"Host: h\$i.fd.test\" http://ll-nginx-fd:8100/missing; done"
FD_AFTER="$(worker_fds)"
FD_PER_BLOCK="$(awk -v a="$FD_AFTER" -v b="$FD_BEFORE" -v n="$FD_BLOCKS" 'BEGIN { printf "%.2f", (a - b) / n }')"
echo "info  fds in the worker: $FD_BEFORE before traffic, $FD_AFTER after $FD_BLOCKS blocks served ($FD_PER_BLOCK per block)"
# The relay access target is shared by every block; each block restates only
# its error targets (the relay's and CrowdSec's): two sockets, at most. The
# slack of 10 is for what is opened once: the shared access targets, and the
# odd client or upstream connection still open at the count.
expect "each server block costs a worker at most 2 sockets" yes \
  "$(awk -v a="$FD_AFTER" -v b="$FD_BEFORE" -v n="$FD_BLOCKS" 'BEGIN { print (a - b <= 2 * n + 10) ? "yes" : "no" }')"
expect "the worker may open 65535 files" 65535 \
  "$(docker exec ll-nginx-fd sh -c 'awk "/Max open files/ { print \$4 }" /proc/$(pgrep -f "nginx: worker" | head -1)/limits')"
docker rm -f -v ll-nginx-fd >/dev/null

# --- 1c. A flood on one watched host: capped at the relay, never silent ---------
# 1,500 requests on one connection, far past the relay's 300 lines a second per
# host. What passes is at most 300 for each second the flood spans; the rest
# arrive as `skipped` counts, about once a second, and together they account for
# every request.
docker run -d --name ll-sub3 --network "$NET" redis:7-alpine \
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:p1 >/dev/null
for _ in $(seq 20); do [ "$(numsub p1)" = 2 ] && break; sleep 0.5; done
sleep 1.2 # a fresh second for the relay's window, past anything earlier
FLOOD=1500
T0="$(date +%s.%N)"
docker exec ll-client curl -s -o /dev/null "http://ll-nginx-a:8001/flood-[1-$FLOOD]" || true
T1="$(date +%s.%N)"
sleep 3 # the skipped count is published a second after the first skip
docker logs ll-sub3 > "$WORK/sub3.out" 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" "$IMAGE" \
  /w/summarize.lua /w/sub3.out > "$WORK/sum3.tsv"
FLOOD_LINES="$(awk -F'\t' '$3 == "request" && $7 ~ /^\/flood-/' "$WORK/sum3.tsv" | wc -l | tr -d ' ')"
FLOOD_SKIPS="$(awk -F'\t' '$1 == "node-a" && $2 == "p1" && $3 == "skipped"' "$WORK/sum3.tsv" | wc -l | tr -d ' ')"
FLOOD_SKIPPED="$(awk -F'\t' '$3 == "skipped" { n += $12 } END { print n + 0 }' "$WORK/sum3.tsv")"
# One more second than the flood spans: a line is counted when the relay sees
# it, a moment after nginx served it.
FLOOD_SECONDS="$(awk -v a="$T0" -v b="$T1" 'BEGIN { print int(b) - int(a) + 2 }')"
echo "info  flood: $FLOOD requests in $(awk -v a="$T0" -v b="$T1" 'BEGIN { printf "%.2f", b - a }') s:" \
  "$FLOOD_LINES published, $FLOOD_SKIPPED skipped in $FLOOD_SKIPS messages"
expect "a flood is held to 300 lines a second" yes \
  "$(awk -v n="$FLOOD_LINES" -v s="$FLOOD_SECONDS" 'BEGIN { print (n >= 300 && n <= 300 * s) ? "yes" : "no" }')"
expect "what the cap drops is reported as skipped" yes \
  "$( [ "$FLOOD_SKIPS" -ge 1 ] && [ "$FLOOD_SKIPS" -le "$FLOOD_SECONDS" ] && echo yes || echo no)"
expect "published plus skipped is every request" "$FLOOD" "$((FLOOD_LINES + FLOOD_SKIPPED))"
docker rm -f -v ll-sub3 >/dev/null

# --- 1d. nginx itself (n), and All logs (all) --------------------------------------
# nginx's main error log sends its process messages (reloads, workers starting
# and exiting) at notice, tagged mgm_n; an error line with no host key (tag
# mgm_x) is nginx's own too. All logs is one more channel: every line goes to
# its host's channel if watched, and to megoopm:live:all if that is watched.
# ll-sub still watches p1, so a p1 line arrives once on each of the two.
docker run -d --name ll-sub-n --network "$NET" redis:7-alpine \
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:n >/dev/null
docker run -d --name ll-sub-all --network "$NET" redis:7-alpine \
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:all >/dev/null
for _ in $(seq 20); do [ "$(numsub n)" = 1 ] && [ "$(numsub all)" = 1 ] && break; sleep 0.5; done
sleep 1.2 # past the relay's cached "nobody watches all" from the flood
for node in a b; do
  expect "a request on node-$node, watched as p1 and as all" 200 "$(curl_ "http://ll-nginx-$node:8001/both-$node")"
done
# Before the relay listens (nginx's own start), a send to it is refused and
# nginx says so in the container log; once it listens, a reload's are not.
refused() { docker logs ll-nginx-a 2>&1 | grep -c 'while logging to syslog' || true; }
REFUSED_BEFORE="$(refused)"
docker exec ll-nginx-a openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -s reload \
  >/dev/null 2>&1 && RC=0 || RC=$?
expect "reload on node-a" 0 "$RC"
# What an error line with no host key looks like on the wire, straight to node-b's relay.
docker exec ll-nginx-b sh -c "printf '%s' '<187>Oct 25 10:00:00 mgm_x: 2026/10/25 10:00:00 [error] 7#7: *1 probe-no-key' \
  | socat - UDP:127.0.0.1:5140"
sleep 2
for s in sub-n sub-all sub; do
  docker logs "ll-$s" > "$WORK/$s.out" 2>&1
  MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" "$IMAGE" \
    /w/summarize.lua "/w/$s.out" > "$WORK/$s.tsv"
done
rows_in() { awk -F'\t' "$2" "$WORK/$1.tsv" | wc -l | tr -d ' '; }
expect "a reload yields a notice-level process line under n" yes \
  "$( [ "$(rows_in sub-n '$1 == "node-a" && $2 == "n" && $3 == "error" && $13 == "5" && $10 ~ /reconfiguring/')" -ge 1 ] \
     && echo yes || echo no)"
expect "an error with no host key arrives under n" 1 \
  "$(rows_in sub-n '$1 == "node-b" && $2 == "n" && $3 == "error" && $10 ~ /probe-no-key/')"
expect "n carries nginx's own lines only" 0 "$(rows_in sub-n '$2 != "n"')"
# The relay's own server logs to stderr, not to the main error log: a relay
# that fed itself would never stop.
expect "nginx's own lines do not feed themselves" yes \
  "$( [ "$(rows_in sub-n '$2 == "n"')" -lt 100 ] && echo yes || echo no)"
expect "the main log's notices stay out of the container log, as before" 0 \
  "$(docker logs ll-nginx-a 2>&1 | grep -c 'reconfiguring' || true)"
expect "a reload's own lines reach the relay, none refused" "$REFUSED_BEFORE" "$(refused)"
for node in a b; do
  expect "all: the request on node-$node arrives once, as host p1" 1 \
    "$(rows_in sub-all "\$1 == \"node-$node\" && \$2 == \"p1\" && \$3 == \"request\" && \$7 == \"/both-$node\"")"
  expect "p1: the same request arrives once on p1's channel too" 1 \
    "$(rows_in sub "\$1 == \"node-$node\" && \$2 == \"p1\" && \$3 == \"request\" && \$7 == \"/both-$node\"")"
done
expect "all: nginx's own lines are in it" yes \
  "$( [ "$(rows_in sub-all '$2 == "n" && $10 ~ /reconfiguring/')" -ge 1 ] && echo yes || echo no)"
expect "all: nothing is published as host all" 0 "$(rows_in sub-all '$2 == "all" && $3 != "skipped"')"

# --- 1e. A flood on All logs: its own cap of 300 lines a second -------------------
# Like 1c, on the all channel: what passes is at most 300 for each second, the
# rest arrive as `skipped` counts with host "all", and the two add up.
docker run -d --name ll-sub-all2 --network "$NET" redis:7-alpine \
  redis-cli -h ll-redis SUBSCRIBE megoopm:live:all >/dev/null
for _ in $(seq 20); do [ "$(numsub all)" = 2 ] && break; sleep 0.5; done
sleep 1.2
T0="$(date +%s.%N)"
docker exec ll-client curl -s -o /dev/null "http://ll-nginx-a:8001/flood-all-[1-$FLOOD]" || true
T1="$(date +%s.%N)"
sleep 3
docker logs ll-sub-all2 > "$WORK/sub-all2.out" 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" "$IMAGE" \
  /w/summarize.lua /w/sub-all2.out > "$WORK/sub-all2.tsv"
ALL_LINES="$(rows_in sub-all2 '$3 == "request" && $7 ~ /^\/flood-all-/')"
ALL_SKIPS="$(rows_in sub-all2 '$1 == "node-a" && $2 == "all" && $3 == "skipped"')"
ALL_SKIPPED="$(awk -F'\t' '$3 == "skipped" && $2 == "all" { n += $12 } END { print n + 0 }' "$WORK/sub-all2.tsv")"
ALL_SECONDS="$(awk -v a="$T0" -v b="$T1" 'BEGIN { print int(b) - int(a) + 2 }')"
echo "info  flood on all: $FLOOD requests in $(awk -v a="$T0" -v b="$T1" 'BEGIN { printf "%.2f", b - a }') s:" \
  "$ALL_LINES published, $ALL_SKIPPED skipped in $ALL_SKIPS messages"
expect "all: a flood is held to 300 lines a second" yes \
  "$(awk -v n="$ALL_LINES" -v s="$ALL_SECONDS" 'BEGIN { print (n >= 300 && n <= 300 * s) ? "yes" : "no" }')"
expect "all: what the cap drops is reported as skipped" yes \
  "$( [ "$ALL_SKIPS" -ge 1 ] && [ "$ALL_SKIPS" -le "$ALL_SECONDS" ] && echo yes || echo no)"
expect "all: published plus skipped is every request" "$FLOOD" "$((ALL_LINES + ALL_SKIPPED))"
docker rm -f -v ll-sub-all2 >/dev/null

# --- 2. Parsing, case by case (the pure M.parse) --------------------------------
cat > "$WORK/parse.lua" <<'EOF'
package.path = "/lua/?.lua;" .. package.path
local relay = require("megoopm_live_relay")
local failed = false
local NONE = {} -- "this field must be absent"
local function matches(got, want)
    if got == nil then return false end
    for k, v in pairs(want) do
        if v == NONE then
            if got[k] ~= nil then return false end
        elseif type(v) == "table" then
            if type(got[k]) ~= "table" or not matches(got[k], v) then return false end
        elseif got[k] ~= v then
            return false
        end
    end
    return true
end
local function check(label, datagram, want)
    local got = relay.parse(datagram)
    local ok
    if want == nil then
        ok = got == nil
    else
        ok = matches(got, want)
    end
    print((ok and "ok    " or "FAIL  ") .. "parse: " .. label)
    if not ok then failed = true end
end
local H = "<190>Oct 25 10:00:00 "
-- Access lines share the tag mgm_x and name their host first, in "k".
check("a request line", H .. 'mgm_x: {"k":"p1","st":"200"}',
    { host = "p1", log = "request", sev = 6, truncated = false, f = { st = "200", k = NONE } })
check("a single-digit day", '<190>Oct  5 10:00:00 mgm_x: {"k":"r22","st":"301"}', { host = "r22", log = "request" })
check("a cut-off request line", H .. 'mgm_x: {"k":"p1","st":"200","u":"/aaa',
    { host = "p1", log = "request", truncated = true, body = '{"k":"p1","st":"200","u":"/aaa' })
check("a stream line", H .. 'mgm_x: {"k":"s4","st":"200"}', { host = "s4", log = "stream" })
check("a line with an empty key (not a host) is dropped", H .. 'mgm_x: {"k":"","st":"200"}', nil)
check("an access line without a key is dropped", H .. 'mgm_x: {"st":"200"}', nil)
check("a client cannot name the host",
    H .. 'mgm_x: {"k":"","u":"/\\",\\"k\\":\\"p1","st":"200"}', nil)
check("a block in the earlier shape is its tag's host", H .. 'mgm_p7: {"k":"u","st":"200"}',
    { host = "p7", log = "request" })
-- nginx itself (host n): an error line under the shared tag carries no host
-- key, and the main error log (nginx's process messages) is tagged mgm_n.
check("an error line under the shared tag is nginx's own",
    "<187>Oct 25 10:00:00 mgm_x: 2026/10/25 10:00:00 [error] 7#7: *1 connect() failed",
    { host = "n", log = "error", sev = 3, truncated = false })
check("a process message at notice is nginx's own",
    "<189>Oct 25 10:00:00 mgm_n: 2026/10/25 10:00:00 [notice] 1#1: signal 1 (SIGHUP) received from 9, reconfiguring",
    { host = "n", log = "error", sev = 5 })
check("a worker crash is nginx's own",
    "<185>Oct 25 10:00:00 mgm_n: 2026/10/25 10:00:00 [alert] 1#1: worker process 8 exited on signal 11",
    { host = "n", log = "error", sev = 1 })
check("nginx's own info noise is dropped",
    H .. "mgm_n: 2026/10/25 10:00:00 [info] 1#1: *1 client closed connection", nil)
check("info under the shared tag is dropped",
    H .. "mgm_x: 2026/10/25 10:00:00 [info] 7#7: *1 client closed connection", nil)
-- `all` is a channel, never a host: nothing may be published as host "all".
check("an access line keyed all is dropped", H .. 'mgm_x: {"k":"all","st":"200"}', nil)
check("an error line tagged all is dropped",
    "<187>Oct 25 10:00:00 mgm_all: 2026/10/25 10:00:00 [error] 7#7: *1 connect() failed", nil)
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
[ "$(grep -c '^ok' "$WORK/parse.out" || true)" = 29 ] || { echo "FAIL  parse: not every case ran"; FAILED=1; }

# --- 2a. The cap and the skipped count, step by step (M.handle) -----------------
# A fixed clock, a fake socket, timers that run when told, and a fake Redis that
# records what is published: the relay's own logic, with nothing else in it.
cat > "$WORK/cap.lua" <<'EOF'
package.path = "/lua/?.lua;" .. package.path
package.loaded["megoopm_live_conf"] = { enabled = true, node = "t", redis_host = "127.0.0.1", redis_port = 1 }
local published = {}
package.loaded["resty.redis"] = { new = function()
    return {
        set_timeouts = function() end,
        connect = function() return true end,
        pubsub = function() return { "numsub", 1 } end,
        publish = function(_, channel, msg) published[#published + 1] = { channel, msg }; return 1 end,
        set_keepalive = function() end,
        close = function() end,
    }
end }
local relay = require("megoopm_live_relay")
local failed = false
local function expect(label, want, got)
    local ok = want == got
    print((ok and "ok    " or "FAIL  ") .. "cap: " .. label .. (ok and "" or (": expected "
        .. tostring(want) .. ", got " .. tostring(got))))
    if not ok then failed = true end
end
local datagram
ngx.req.socket = function() return { receive = function() return datagram end } end
local now = 1000.5
ngx.now = function() return now end
local timers, fail_line_timers = {}, false
ngx.timer.at = function(delay, fn, ...)
    if delay == 0 and fail_line_timers then return nil, "too many pending timers" end
    timers[#timers + 1] = { delay = delay, fn = fn, args = { ... } }
    return true
end
local function run(delay)
    local n = 0
    for _, t in ipairs(timers) do
        if t.delay == delay then t.fn(false, unpack(t.args)); n = n + 1 end
    end
    return n
end
local function skipped_messages()
    local out = {}
    for _, p in ipairs(published) do
        if p[2]:find('"skipped"', 1, true) then out[#out + 1] = p end
    end
    return out
end
local function on(channel)
    local n = 0
    for _, p in ipairs(published) do
        if p[1] == channel then n = n + 1 end
    end
    return n
end
local shm = ngx.shared.megoopm_live
shm:set("p1", 1, 60) -- the cached NUMSUB count: somebody watches p1
shm:set("all", 0, 60) -- and nobody watches All logs, until the cases below
datagram = '<190>Oct 25 10:00:00 mgm_x: {"k":"p1","st":"200"}'

for _ = 1, 305 do relay.handle() end
expect("300 lines in one second are relayed", 300, run(0))
expect("one skipped report is due", 1, run(1))
local skips = skipped_messages()
expect("one skipped message", 1, #skips)
expect("on the host's channel", "megoopm:live:p1", skips[1] and skips[1][1])
expect("counting the 5 over the cap", '{"log":"skipped","node":"t","host":"p1","count":5}', skips[1] and skips[1][2])

now, timers, published = 1001.5, {}, {}
for _ = 1, 3 do relay.handle() end
expect("the next second starts a new count", 3, run(0))
expect("and nothing was skipped in it", 0, run(1))

now, timers, published, fail_line_timers = 1002.5, {}, {}, true
relay.handle()
fail_line_timers = false
expect("a line whose timer could not start is not lost silently", 1, run(1))
skips = skipped_messages()
expect("it is counted as skipped", '{"log":"skipped","node":"t","host":"p1","count":1}', skips[1] and skips[1][2])

now, timers, published = 1003.5, {}, {}
shm:set("p9", 0, 60) -- nobody watches p9
datagram = '<190>Oct 25 10:00:00 mgm_x: {"k":"p9","st":"200"}'
for _ = 1, 400 do relay.handle() end
expect("an unwatched host costs no timer", 0, #timers)

-- All logs: every line also goes to megoopm:live:all, under a cap of its own.
now, timers, published = 1004.5, {}, {}
shm:set("all", 1, 60) -- somebody watches All logs; still nobody p9
for _ = 1, 305 do relay.handle() end
expect("all: 300 lines of a host nobody else watches are relayed", 300, run(0))
expect("all: on the all channel", 300, on("megoopm:live:all"))
expect("all: and not on the host's", 0, on("megoopm:live:p9"))
expect("all: each keeps its real host", true, published[1] and published[1][2]:find('"host":"p9"', 1, true) ~= nil)
published = {}
expect("all: one skipped report is due", 1, run(1))
skips = skipped_messages()
expect("all: one skipped message", 1, #skips)
expect("all: on the all channel", "megoopm:live:all", skips[1] and skips[1][1])
expect("all: counting the 5 over its cap", '{"log":"skipped","node":"t","host":"all","count":5}',
    skips[1] and skips[1][2])

now, timers, published = 1005.5, {}, {}
datagram = '<190>Oct 25 10:00:00 mgm_x: {"k":"p1","st":"200"}'
relay.handle()
expect("both watched: one line is one timer", 1, run(0))
expect("both watched: published on the host's channel", 1, on("megoopm:live:p1"))
expect("both watched: and on all", 1, on("megoopm:live:all"))
expect("both watched: the same message", true, published[2] ~= nil and published[1][2] == published[2][2])

-- nginx itself: an error line with no host key is host n.
now, timers, published = 1006.5, {}, {}
shm:set("all", 0, 60)
shm:set("n", 1, 60)
datagram = "<187>Oct 25 10:00:00 mgm_x: 2026/10/25 10:00:00 [error] 7#7: *1 no host here"
relay.handle()
run(0)
expect("n: an error with no host key is published as nginx's own", 1, on("megoopm:live:n"))
expect("n: as host n", true, published[1] and published[1][2]:find('"host":"n"', 1, true) ~= nil)

now, timers, published = 1007.5, {}, {}
shm:set("n", 0, 60)
for _ = 1, 10 do relay.handle() end
expect("neither n nor all watched: no timer", 0, #timers)
os.exit(failed and 1 or 0)
EOF
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" \
  -v "$REPO/infra/nginx/lua:/lua:ro" "$IMAGE" --shdict 'megoopm_live 1m' /w/cap.lua > "$WORK/cap.out" 2>&1 || true
cat "$WORK/cap.out"
grep -q '^FAIL' "$WORK/cap.out" && FAILED=1
[ "$(grep -c '^ok' "$WORK/cap.out" || true)" = 25 ] || { echo "FAIL  cap: not every case ran"; FAILED=1; }

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
  "$(docker logs ll-nginx-c 2>&1 | grep -c 'uninitialized "mgm_' || true)"
# Nodes a and b have no bouncer: their request lines never carry a mark.
expect "requests that were never denied carry cs empty" 0 "$(rows '$3 == "request" && $11 != ""')"

# --- 3. Nobody watching: the relay asks, and publishes nothing ------------------
docker rm -f -v ll-sub >/dev/null
for _ in $(seq 20); do [ "$(numsub p1)" = 0 ] && break; sleep 0.5; done
sleep 2 # past the 1 s NUMSUB cache
# All logs alone: nobody watches p1 itself, and its lines still reach all.
expect "all only: a request on p1" 200 "$(curl_ "http://ll-nginx-a:8001/all-only")"
sleep 1
docker logs ll-sub-all > "$WORK/sub-all.out" 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint resty -v "$WORK_MOUNT:/w" "$IMAGE" \
  /w/summarize.lua /w/sub-all.out > "$WORK/sub-all.tsv"
expect "all only: it arrives on all, as host p1" 1 \
  "$(rows_in sub-all '$1 == "node-a" && $2 == "p1" && $3 == "request" && $7 == "/all-only"')"
docker rm -f -v ll-sub-n ll-sub-all >/dev/null
for _ in $(seq 20); do [ "$(numsub all)" = 0 ] && [ "$(numsub n)" = 0 ] && break; sleep 0.5; done
sleep 2 # past the 1 s NUMSUB cache
docker run -d --name ll-monitor --network "$NET" redis:7-alpine redis-cli -h ll-redis MONITOR >/dev/null
sleep 1
for node in a b; do
  curl_ "http://ll-nginx-$node:8001/unwatched" >/dev/null
  curl_ "http://ll-nginx-$node/scanner-probe" >/dev/null # the base default server, tag mgm_u
  # nginx's own: a line for n, which nobody watches either.
  docker exec "ll-nginx-$node" sh -c "printf '%s' '<187>Oct 25 10:00:00 mgm_x: 2026/10/25 10:00:00 [error] 7#7: *1 unwatched-n' \
    | socat - UDP:127.0.0.1:5140"
done
sleep 2
docker logs ll-monitor > "$WORK/monitor.out" 2>&1
expect "unwatched: no PUBLISH" 0 "$(grep -ci '"publish"' "$WORK/monitor.out" || true)"
expect "unwatched: all is asked about" yes \
  "$(grep -qi '"pubsub" "numsub" "megoopm:live:all"' "$WORK/monitor.out" && echo yes || echo no)"
expect "unwatched: n is asked about" yes \
  "$(grep -qi '"pubsub" "numsub" "megoopm:live:n"' "$WORK/monitor.out" && echo yes || echo no)"
expect "unwatched: p1 is asked about" yes \
  "$(grep -qi '"pubsub" "numsub" "megoopm:live:p1"' "$WORK/monitor.out" && echo yes || echo no)"
expect "unwatched: the default server (u) is asked about" yes \
  "$(grep -qi '"pubsub" "numsub" "megoopm:live:u"' "$WORK/monitor.out" && echo yes || echo no)"

# The default server's bouncer denies these probes (no CrowdSec here: AppSec
# fails closed) before `set $mgm_cs` runs; that must not warn on every hit.
expect "a denied default-server request logs no warning" 0 \
  "$(docker logs ll-nginx-a 2>&1 | grep -c 'uninitialized "mgm_' || true)"

# --- 4. Redis down: nothing fails, nothing blocks (Review Focus 1) --------------
docker rm -f -v ll-monitor ll-redis >/dev/null
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
