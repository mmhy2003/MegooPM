#!/usr/bin/env bash
# End-to-end check of what a static site serves: the real template rendered by
# the backend, run by the nginx image over a fixture folder mounted read-only at
# /sites, probed with curl on an internal network. Offline, no CrowdSec.
#
# The fixture lives in a Docker volume, not a bind mount, so it can hold a real
# symlink on any host. The working tree's backend code, nginx.conf and Lua are
# mounted over the images' copies, and both images are rebuilt first, so a run
# always tests what is on disk. Exit status is the verdict.
#
#   MSYS_NO_PATHCONV=1 bash infra/nginx/tests/static-sites.sh
set -euo pipefail

# `pwd -W` gives Docker Desktop a Windows path under Git Bash; plain pwd elsewhere.
REPO="$(cd "$(dirname "$0")/../../.." && { pwd -W 2>/dev/null || pwd; })"
NET=megoopm-static-probe
NGINX_IMAGE=megoopm-nginx-probe
BACKEND_IMAGE=megoopm-backend-probe
SITES=megoopm-static-probe-sites
WORK="$(mktemp -d)"
# Docker Desktop cannot mount a Git Bash /tmp path; cygpath gives it the Windows one.
WORK_MOUNT="$(cygpath -m "$WORK" 2>/dev/null || echo "$WORK")"
CONTAINERS="ss-nginx ss-client"

cleanup() {
  # shellcheck disable=SC2086
  docker rm -f -v $CONTAINERS >/dev/null 2>&1 || true
  docker volume rm "$SITES" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
cleanup
mkdir -p "$WORK"

docker build -q -t "$NGINX_IMAGE" "$REPO/infra/nginx" >/dev/null
docker build -q -t "$BACKEND_IMAGE" "$REPO/backend" >/dev/null
docker network create --internal "$NET" >/dev/null

fail() { echo "FAIL  $1"; exit 1; }
ok() { echo "ok    $1"; }

# --- the fixture ---------------------------------------------------------------
# `site` has its own 404.html; `bare` does not, so it gets the branded page.
docker volume create "$SITES" >/dev/null
docker run --rm -v "$SITES:/sites" "$NGINX_IMAGE" sh -c '
  set -e
  mkdir -p /sites/site/about /sites/site/.git /sites/site/.well-known /sites/bare
  echo SITE-INDEX > /sites/site/index.html
  echo SITE-ABOUT > /sites/site/about/index.html
  echo SITE-404 > /sites/site/404.html
  echo "[core]" > /sites/site/.git/config
  echo "Contact: mailto:security@site.test" > /sites/site/.well-known/security.txt
  ln -s /etc/passwd /sites/site/link
  echo BARE-INDEX > /sites/bare/index.html
  ln -s /sites/site /sites/linked
  chmod -R a+rX /sites
' >/dev/null

# --- the rendered config ---------------------------------------------------------
# The renderer is a pure function of the desired state, so no database is needed.
cat > "$WORK/render.py" <<'EOF'
import os

from app.services.nginx import render_config, render_default_site
from app.services.nginx.state import DesiredState, StaticSiteSpec

state = DesiredState(
    static_sites=(
        StaticSiteSpec(id=1, domain_names=("site.test",), folder="site", crowdsec_enabled=False),
        StaticSiteSpec(id=2, domain_names=("bare.test",), folder="bare", crowdsec_enabled=False),
        StaticSiteSpec(id=3, domain_names=("linked.test",), folder="linked", crowdsec_enabled=False),
    )
)
for sub, files in (("conf.d", render_config(state)), ("default", render_default_site(state))):
    out = f"/out/nginx/{sub}"
    os.makedirs(out, exist_ok=True)
    for name, body in files.items():
        with open(f"{out}/{name}", "w") as fh:
            fh.write(body)
EOF
mkdir -p "$WORK/data"
MSYS_NO_PATHCONV=1 docker run --rm --user 0 --entrypoint python \
  -e DATABASE_URL=postgresql+asyncpg://probe:probe@localhost/probe -e PYTHONPATH=/app \
  -v "$REPO/backend/app:/app/app:ro" -v "$WORK_MOUNT/render.py:/render.py:ro" \
  -v "$WORK_MOUNT/data:/out" "$BACKEND_IMAGE" /render.py
[ -f "$WORK/data/nginx/conf.d/megoopm-static-1.conf" ] || fail "the backend rendered no static site config"

# --- nginx -------------------------------------------------------------------------
# File by file, not the directory: the entrypoint writes into /etc/nginx/lua.
LUA=()
for f in "$REPO"/infra/nginx/lua/*.lua; do
  LUA+=(-v "$f:/etc/nginx/lua/$(basename "$f"):ro")
done
MSYS_NO_PATHCONV=1 docker run -d --name ss-nginx --network "$NET" \
  -e NODE_ID=probe -e REDIS_URL=redis://localhost:6379/0 "${LUA[@]}" \
  -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$WORK_MOUNT/data:/data" -v "$SITES:/sites:ro" "$NGINX_IMAGE" >/dev/null
docker run -d --name ss-client --network "$NET" --entrypoint sleep curlimages/curl:8.11.1 600 >/dev/null

probe() { # host path -> "<status> <first body line> <location>"
  docker exec ss-client sh -c \
    "curl -s -o /tmp/body -w '%{http_code} %{redirect_url}' -H 'Host: $1' 'http://ss-nginx$2'; echo; cat /tmp/body" \
    || true
}
for _ in $(seq 30); do
  [ "$(probe site.test / | head -1 | cut -d' ' -f1)" = 200 ] && break
  sleep 0.5
done

check() { # label host path status [body-grep] [location-suffix]
  local out status body
  out="$(probe "$2" "$3")"
  status="$(echo "$out" | head -1 | cut -d' ' -f1)"
  body="$(echo "$out" | tail -n +2)"
  [ "$status" = "$4" ] || { docker logs --tail 20 ss-nginx 2>&1 | tail -20; fail "$1: expected $4, got $status"; }
  if [ -n "${5:-}" ]; then
    echo "$body" | grep -q -- "$5" || fail "$1: body lacks \"$5\": $(echo "$body" | head -3)"
  fi
  if [ -n "${6:-}" ]; then
    case "$(echo "$out" | head -1 | cut -d' ' -f2)" in
      *"$6") ;;
      *) fail "$1: redirect does not end in $6: $(echo "$out" | head -1)" ;;
    esac
  fi
  ok "$1 ($status)"
}

check "the index" site.test / 200 SITE-INDEX
check "a folder without its slash redirects" site.test /about 301 "" /about/
check "a folder's index" site.test /about/ 200 SITE-ABOUT
check "a missing page shows the site's 404.html" site.test /nope 404 SITE-404
check "a missing page on a site without one shows the branded 404" bare.test /nope 404 "nothing at this address"
check "a dot-folder is hidden" site.test /.git/config 404 SITE-404
check "/.well-known/ is served" site.test /.well-known/security.txt 200 "Contact:"
# try_files treats a refused symlink as missing: the site's 404, never the target.
check "a symlink is refused" site.test /link 404 SITE-404
[ -z "$(probe site.test /link | grep 'root:')" ] || fail "a symlink served its target"
check "no extension fallback" site.test /about.html 404 SITE-404
check "a symlinked site folder serves nothing" linked.test / 404 "nothing at this address"

echo "PASS static-sites"
