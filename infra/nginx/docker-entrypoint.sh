#!/bin/sh
# Entrypoint for the CrowdSec-enabled managed nginx proxy (MEG-22).
#
# Renders the bouncer config from the environment (so the LAPI URL / bouncer key
# / AppSec URL are never baked into the image), then execs nginx (OpenResty).
set -eu

: "${CROWDSEC_LAPI_URL:=http://crowdsec:8080}"
: "${CROWDSEC_APPSEC_URL:=http://crowdsec:7422}"
: "${CROWDSEC_BOUNCER_KEY:=}"

if [ -z "${CROWDSEC_BOUNCER_KEY}" ]; then
    echo "[megoopm] WARNING: CROWDSEC_BOUNCER_KEY is empty — the bouncer will" \
         "fail to authenticate to LAPI. Register one with 'cscli bouncers add'." >&2
fi

export CROWDSEC_LAPI_URL CROWDSEC_APPSEC_URL CROWDSEC_BOUNCER_KEY
envsubst '${CROWDSEC_LAPI_URL} ${CROWDSEC_APPSEC_URL} ${CROWDSEC_BOUNCER_KEY}' \
    < /etc/nginx/crowdsec-bouncer.conf.template \
    > /etc/nginx/crowdsec-bouncer.conf

# --- Reload agent (worker <-> nginx control channel, socket-free) ---
# socat spawns /reload-agent.sh per connection on :9099 (never published, so
# only the compose network can reach it). Runs in a restart loop so a crashed
# socat comes back within a second; the container healthcheck pings it.
: "${NGINX_RELOAD_TOKEN:=}"
if [ -z "${NGINX_RELOAD_TOKEN}" ]; then
    echo "[megoopm] WARNING: NGINX_RELOAD_TOKEN is empty — the reload agent will" \
         "refuse every request, so the worker cannot validate/reload this nginx." >&2
fi
export NGINX_RELOAD_TOKEN
(
    while true; do
        socat TCP-LISTEN:9099,fork,reuseaddr EXEC:/reload-agent.sh
        sleep 1
    done
) &

exec "$@"
