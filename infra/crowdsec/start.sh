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
hooks=${MEGOOPM_HOOKS:-/tmp/megoopm-hooks.sh}
hub_probe=${MEGOOPM_HUB_PROBE_URL:-https://cdn-hub.crowdsec.net/crowdsecurity/master/.index.json}
mirror_probe=${MEGOOPM_MIRROR_PROBE_URL:-https://raw.githubusercontent.com/crowdsecurity/hub/master/.index.json}
# CrowdSec fills in the hub branch and the item path.
mirror_template='https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s'
# The stock script's hub steps, where they are called. Their definitions
# ("name() {") do not match, and neither do the helpers they call.
steps='cscli_if_clean|run_hub_update_if_from_volume|run_hub_upgrade_if_from_volume'

log() { echo "megoopm: $*" >&2; }

probe() {
    if ! command -v wget >/dev/null 2>&1; then
        log "wget is missing, so the hub was not probed; trying it anyway"
        return 0
    fi
    # Headers only: the index is megabytes, and pulling it on every start is
    # the kind of traffic that gets an address blocked.
    timeout 15 wget -q --spider -T 5 "$1" >/dev/null 2>&1
}

# official: CrowdSec's own hub; mirror: GitHub's copy of it, for when the
# official one refuses this address; none: neither answers.
hub_state() {
    case "${MEGOOPM_HUB_MODE:-auto}" in
        online) echo official; return ;;
        offline) echo offline; return ;;
    esac
    if probe "$hub_probe"; then
        echo official
    elif probe "$mirror_probe"; then
        echo mirror
    else
        echo none
    fi
}

if ! grep -qE "^ *($steps) " "$stock"; then
    log "no hub steps found in $stock (has the image changed?); running it unchanged"
    exec /bin/bash "$stock" "$@"
fi

state=$(hub_state)
case "$state" in
    official | mirror)
        replacement='\1\2 \3 || echo "megoopm: hub step failed, starting on installed items: \2" >\&2'
        ;;
    *)
        log "CrowdSec's hub is unreachable ($hub_probe): skipping hub updates and installs, starting on installed items"
        replacement='\1echo "megoopm: skipped, hub unreachable: \2" >\&2'
        ;;
esac

# Sourced by the patched script, where its conf_set and the config file it
# edits exist: on a fresh volume, config.yaml is only copied in there.
{
    printf 'megoopm_hub_state=%q\n' "$state"
    printf 'megoopm_hub_probe=%q\n' "$hub_probe"
    printf 'megoopm_mirror_probe=%q\n' "$mirror_probe"
    printf 'megoopm_mirror_template=%q\n' "$mirror_template"
    cat <<'HOOKS'
log() { echo "megoopm: $*" >&2; }

megoopm_before_hub() {
    case "$megoopm_hub_state" in
        official)
            # Only our own value: an address someone set by hand stays.
            conf_set "del(.cscli.__hub_url_template__ | select(. == \"$megoopm_mirror_template\"))" ||
                log "could not update the hub URL setting"
            ;;
        mirror)
            log "official hub unreachable, using the GitHub mirror"
            conf_set ".cscli.__hub_url_template__ = \"$megoopm_mirror_template\"" ||
                log "could not update the hub URL setting"
            local applied
            applied=$(cscli config show --key Config.Cscli.HubURLTemplate 2>/dev/null || true)
            case "$applied" in
                *raw.githubusercontent.com*) ;;
                *) log "CrowdSec ignored the hub mirror setting (has the image changed?); the hub steps will likely fail" ;;
            esac
            ;;
    esac
}
HOOKS
} > "$hooks"

if ! grep -qx 'prepare_hub' "$stock"; then
    log "no prepare_hub call found in $stock (has the image changed?); the hub mirror is off"
fi

sed -E \
    -e "s/^( *)($steps) (.*)\$/$replacement/" \
    -e "s|^prepare_hub\$|. '$hooks'; megoopm_before_hub; prepare_hub|" \
    "$stock" > "$patched"
exec "${MEGOOPM_START_EXEC:-/bin/bash}" "$patched" "$@"
