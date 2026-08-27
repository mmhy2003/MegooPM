#!/bin/sh
# MegooPM nginx reload agent — one process per connection, spawned by socat
# (see docker-entrypoint.sh). stdin/stdout are the TCP socket.
#
# Protocol: the client sends ONE line `<token> <ping|test|reload>`. The agent
# runs the matching FIXED command (no arguments ever come from the client),
# streams its combined output back, and ends with `__MEGOOPM_STATUS__ <code>`.
# The worker-side client (backend/scripts/nginx_remote.py) turns that trailer
# into its own exit code so the reload engine sees `nginx -t` semantics.
#
# Exit codes on the trailer: the command's own status; 2 unknown command;
# 77 refused (bad or unset token).
set -u

OPENRESTY_BIN="/usr/local/openresty/bin/openresty"
OPENRESTY_ARGS="-p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf"

finish() {
    printf '__MEGOOPM_STATUS__ %s\n' "$1"
    exit 0
}

# Run one openresty command with stdout+stderr captured to a file, then echo
# the capture. Under socat's EXEC our fd 1 is a socket, and the image's log
# files are symlinks to /dev/stdout & /dev/stderr, which cannot be reopened
# through a socket ("No such device or address") — redirecting to a regular
# file first makes those paths resolve to something openable.
run_openresty() {
    out=$(mktemp)
    # shellcheck disable=SC2086  # OPENRESTY_ARGS is intentionally word-split
    "$OPENRESTY_BIN" $OPENRESTY_ARGS "$@" >"$out" 2>&1
    rc=$?
    cat "$out"
    rm -f "$out"
    return $rc
}

# shellcheck disable=SC2034  # _rest swallows anything after the command word
if ! read -r token cmd _rest; then
    echo "reload agent: empty request"
    finish 2
fi

if [ -z "${NGINX_RELOAD_TOKEN:-}" ]; then
    echo "reload agent: NGINX_RELOAD_TOKEN is not set on the nginx container; refusing"
    finish 77
fi
if [ "$token" != "$NGINX_RELOAD_TOKEN" ]; then
    echo "reload agent: bad token"
    finish 77
fi

case "$cmd" in
    ping)
        echo pong
        finish 0
        ;;
    test)
        run_openresty -t
        finish $?
        ;;
    reload)
        run_openresty -s reload
        finish $?
        ;;
    *)
        echo "reload agent: unknown command '$cmd'"
        finish 2
        ;;
esac
