#!/usr/bin/env bash
# End-to-end check of the CrowdSec bouncer in nginx's server-rewrite phase.
#
# Runs a real CrowdSec 1.6.4 and the MegooPM nginx image on a private network
# and compares, request by request, the access-phase hook proxy hosts use
# (reference, :8001) with the server-rewrite hook (:8002 server-level return,
# :8003 location-level return). The working tree's Lua is mounted over the
# image's copy, so a run always tests what is on disk. Exit status is the verdict.
#
#   MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh
set -euo pipefail

# `pwd -W` gives Docker Desktop a Windows path under Git Bash; plain pwd elsewhere.
REPO="$(cd "$(dirname "$0")/../../.." && { pwd -W 2>/dev/null || pwd; })"
NET=megoopm-bouncer-probe
KEY=probe-bouncer-key-0123456789abcdef0123
WORK="$(mktemp -d)"
# Docker Desktop cannot mount a Git Bash /tmp path; cygpath gives it the Windows one.
WORK_MOUNT="$(cygpath -m "$WORK" 2>/dev/null || echo "$WORK")"
FAILED=0

cleanup() {
  docker rm -f bp-crowdsec bp-nginx bp-client >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
cleanup

docker build -q -t megoopm-nginx-probe "$REPO/infra/nginx" >/dev/null
docker network create "$NET" >/dev/null

docker run -d --name bp-crowdsec --network "$NET" --network-alias crowdsec \
  -e DISABLE_ONLINE_API=true -e BOUNCER_KEY_megoopm="$KEY" \
  -e COLLECTIONS="crowdsecurity/appsec-virtual-patching crowdsecurity/appsec-generic-rules" \
  -v "$REPO/infra/crowdsec/acquis/appsec.yaml:/etc/crowdsec/acquis.d/appsec.yaml:ro" \
  crowdsecurity/crowdsec:v1.6.4 >/dev/null
for _ in $(seq 60); do
  docker exec bp-crowdsec cscli lapi status >/dev/null 2>&1 && break
  sleep 2
done

mkdir -p "$WORK/nginx/conf.d/stream" "$WORK/nginx/default"
cat > "$WORK/nginx/conf.d/probe.conf" <<'EOF'
server { listen 8001; access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
         # Content phase, like a proxy host's proxy_pass. A `return` here would
         # run before the access-phase bouncer and prove nothing.
         location / { content_by_lua_block { ngx.say("reached") } } }
server { listen 8002; server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
         return 302 http://target.example/; }
server { listen 8003; server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
         location / { return 404; } }
EOF

start_nginx() { # extra docker-run args...
  # File by file, not the directory: the entrypoint writes into /etc/nginx/lua.
  local lua=() f
  for f in "${LUA_SRC:-$REPO/infra/nginx/lua}"/*.lua; do
    lua+=(-v "$f:/etc/nginx/lua/$(basename "$f"):ro")
  done
  docker rm -f bp-nginx >/dev/null 2>&1 || true
  docker run -d --name bp-nginx --network "$NET" "$@" "${lua[@]}" \
    -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "$WORK_MOUNT:/data" megoopm-nginx-probe >/dev/null
  sleep 5
}
start_nginx -e CROWDSEC_LAPI_URL=http://crowdsec:8080 -e CROWDSEC_APPSEC_URL=http://crowdsec:7422 \
  -e CROWDSEC_BOUNCER_KEY="$KEY"
docker run -d --name bp-client --network "$NET" --entrypoint sleep curlimages/curl 600 >/dev/null
CLIENT_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" bp-client)"

status() { # port path [curl args...]
  local port="$1" path="$2"; shift 2
  # A refused connection reports 000 rather than ending the run under set -e.
  docker exec bp-client curl -s -o /dev/null -w '%{http_code}' "$@" "http://bp-nginx:$port$path" || true
}
expect() { # label expected actual
  if [ "$2" = "$3" ]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; FAILED=1; fi
}
wait_for_stream() { sleep 12; } # bouncer UPDATE_FREQUENCY=10

# 1. A clean IP reaches each server's own answer.
expect "clean, access reference"      200 "$(status 8001 /)"
expect "clean, server-level return"   302 "$(status 8002 /)"
expect "clean, location-level return" 404 "$(status 8003 /)"

# 2. A POST body is readable in this phase (AppSec reads it) and passes clean.
expect "clean POST, server-level return" 302 "$(status 8002 /form -X POST --data 'name=alice')"

# 3. AppSec blocks a virtual-patching probe in the new phase as in the old.
REF="$(status 8001 /.env)"
expect "AppSec probe, reference blocks"      403 "$REF"
expect "AppSec probe, server-level return"   "$REF" "$(status 8002 /.env)"
expect "AppSec probe, location-level return" "$REF" "$(status 8003 /.env)"

# 4. A ban refuses the client on every hook.
docker exec bp-crowdsec cscli decisions add --ip "$CLIENT_IP" --duration 1h >/dev/null
wait_for_stream
expect "ban, access reference"      403 "$(status 8001 /)"
expect "ban, server-level return"   403 "$(status 8002 /)"
expect "ban, location-level return" 403 "$(status 8003 /)"

# 5. A captcha decision behaves exactly as it does on the reference hook.
docker exec bp-crowdsec cscli decisions delete --ip "$CLIENT_IP" >/dev/null
docker exec bp-crowdsec cscli decisions add --ip "$CLIENT_IP" --type captcha --duration 1h >/dev/null
wait_for_stream
REF="$(status 8001 /)"
expect "captcha, server-level return"   "$REF" "$(status 8002 /)"
expect "captcha, location-level return" "$REF" "$(status 8003 /)"

# 7. The default server (base nginx.conf, port 80) refuses a banned client
#    but keeps answering the healthcheck.
docker exec bp-crowdsec cscli decisions delete --ip "$CLIENT_IP" >/dev/null
docker exec bp-crowdsec cscli decisions add --ip "$CLIENT_IP" --duration 1h >/dev/null
wait_for_stream
expect "ban, default server"          403 "$(status 80 /)"
expect "ban, default server /healthz" 200 "$(status 80 /healthz)"

# 6. With the bouncer uninitialised, requests pass and the error is logged
#    once per worker, not once per request: the default sites call the check
#    for every scanner hit. The init script is swapped for a no-op, which is
#    exactly the state a failed init leaves behind.
mkdir -p "$WORK/lua"
cp "$REPO"/infra/nginx/lua/*.lua "$WORK/lua/"
echo "-- disabled by bouncer-phase.sh" > "$WORK/lua/megoopm_crowdsec_init.lua"
LUA_SRC="$WORK_MOUNT/lua" start_nginx
# More requests than workers, or a per-request logger could pass unnoticed on
# a many-core machine (worker_processes auto).
WORKERS="$(docker exec bp-nginx sh -c 'ps -o args | grep -c "[n]ginx: worker"')"
for _ in $(seq $((WORKERS * 3 + 3))); do status 8002 / >/dev/null; done
expect "uninitialised, request passes" 302 "$(status 8002 /)"
LINES="$(docker logs bp-nginx 2>&1 | grep -c 'bouncer not initialised' || true)"
if [ "$LINES" -le "$WORKERS" ]; then
  echo "ok    uninitialised, logged $LINES time(s) for $WORKERS worker(s)"
else
  echo "FAIL  uninitialised, logged $LINES times for $WORKERS worker(s)"; FAILED=1
fi

[ "$FAILED" = 0 ] && echo "PASS" || { echo "FAILED"; docker logs bp-nginx 2>&1 | tail -30; }
exit "$FAILED"
