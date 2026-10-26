#!/bin/bash
# MegooPM's entrypoint for the CrowdSec container: the image's own
# /docker_start.sh, changed so that CrowdSec still starts when its hub cannot
# be reached.
#
# The stock script refreshes the hub and reinstalls items on every start, and
# exits on the first failure (set -e). So any restart while CrowdSec's servers
# refuse this address (HTTP 403) or cannot be reached at all is a crash loop,
# and because the bouncer fails closed, every protected host denies traffic
# until the hub answers again.
#
# The hub steps are rewritten before the script runs:
#   - hub reachable: they run as before, but a failure is logged, not fatal;
#   - hub unreachable: they are skipped, since each would only wait out its
#     own network timeout, and CrowdSec starts on the items already installed.
# A first start still needs the hub: with nothing installed, CrowdSec cannot
# load its AppSec config and does not start either way.
#
# MEGOOPM_HUB_MODE: auto (default) probes the hub; online / offline force it.
# backend/tests/test_crowdsec_start_script.py runs this against the stock
# script of the pinned image.
set -euo pipefail

stock=${MEGOOPM_STOCK_START:-/docker_start.sh}
patched=${MEGOOPM_PATCHED_START:-/tmp/megoopm-docker_start.sh}
probe_url=https://cdn-hub.crowdsec.net/crowdsecurity/master/.index.json
# The stock script's hub steps, where they are called. Their definitions
# ("name() {") do not match, and neither do the helpers they call.
steps='cscli_if_clean|run_hub_update_if_from_volume|run_hub_upgrade_if_from_volume'

log() { echo "megoopm: $*" >&2; }

hub_reachable() {
    case "${MEGOOPM_HUB_MODE:-auto}" in
        online) return 0 ;;
        offline) return 1 ;;
    esac
    if ! command -v wget >/dev/null 2>&1; then
        log "wget is missing, so the hub was not probed; trying it anyway"
        return 0
    fi
    # Headers only: the index is megabytes, and pulling it on every start is
    # the kind of traffic that gets an address blocked.
    timeout 15 wget -q --spider -T 5 "$probe_url" >/dev/null 2>&1
}

if ! grep -qE "^ *($steps) " "$stock"; then
    log "no hub steps found in $stock (has the image changed?); running it unchanged"
    exec /bin/bash "$stock" "$@"
fi

if hub_reachable; then
    replacement='\1\2 \3 || echo "megoopm: hub step failed, starting on installed items: \2" >\&2'
else
    log "CrowdSec's hub is unreachable ($probe_url): skipping hub updates and installs, starting on installed items"
    replacement='\1echo "megoopm: skipped, hub unreachable: \2" >\&2'
fi

sed -E "s/^( *)($steps) (.*)\$/$replacement/" "$stock" > "$patched"
exec "${MEGOOPM_START_EXEC:-/bin/bash}" "$patched" "$@"
