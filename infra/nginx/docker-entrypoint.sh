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

exec "$@"
