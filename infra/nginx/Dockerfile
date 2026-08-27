# Managed nginx proxy image, extended with the CrowdSec OpenResty bouncer (MEG-22).
#
# We build on OpenResty (nginx + LuaJIT) because the CrowdSec nginx bouncer and
# the inline AppSec/WAF are implemented as Lua run in nginx's access phase. The
# stock `nginx:alpine` has no Lua, so it cannot host the bouncer.
#
# The image stays a drop-in replacement for the previous `nginx:1.27-alpine`
# service: it still reads /etc/nginx/nginx.conf and `include`s the backend's
# managed vhosts from /etc/nginx/conf.d. Secrets are NOT baked in — the
# entrypoint substitutes ${CROWDSEC_*} from the environment at start.
FROM openresty/openresty:1.25.3.2-alpine-fat

# envsubst (gettext) renders the bouncer config from the environment at runtime.
RUN apk add --no-cache gettext ca-certificates

# lua-resty deps the CrowdSec bouncer requires (`resty.http`, `resty.string`).
# These DO live on OPM, but under the right accounts: `resty.http` is published
# by `ledgetech` (NOT `openresty`), and `resty.string` by `openresty`.
RUN opm get ledgetech/lua-resty-http \
    && opm get openresty/lua-resty-string

# The CrowdSec OpenResty bouncer itself is NOT published to OPM (there is no
# `crowdsecurity` OPM account). It is distributed from GitHub, so we clone the
# pinned release and drop its Lua tree into OpenResty's site lualib. That tree is
# `lib/crowdsec.lua` + `lib/plugins/crowdsec/*.lua`, which makes both
# `require("crowdsec")` (our init handler) and the module's internal
# `require("plugins.crowdsec.*")` resolve against the default lua_package_path.
# Pinned so the image is reproducible and the lua API our handlers call is stable.
ARG CS_BOUNCER_VERSION=v1.0.8
RUN apk add --no-cache --virtual .build-deps git \
    && git clone --depth 1 --branch "${CS_BOUNCER_VERSION}" \
        https://github.com/crowdsecurity/lua-cs-bouncer.git /tmp/lua-cs-bouncer \
    && cp -a /tmp/lua-cs-bouncer/lib/. /usr/local/openresty/site/lualib/ \
    && test -f /usr/local/openresty/site/lualib/crowdsec.lua \
    && rm -rf /tmp/lua-cs-bouncer \
    && apk del .build-deps

# OpenResty keeps mime.types under its own prefix; the base nginx.conf includes
# /etc/nginx/mime.types (the vanilla-nginx path), so bridge it.
RUN mkdir -p /etc/nginx \
    && ln -sf /usr/local/openresty/nginx/conf/mime.types /etc/nginx/mime.types

# Drop the stock OpenResty default.conf (MEG-36 Defect B). The base nginx.conf
# already ships the `_` default_server with /healthz; leaving the stock vhost
# (server_name localhost) in conf.d shadows it for `Host: localhost`, so the
# compose healthcheck `wget /healthz` 404s and nginx never turns healthy. On a
# fresh nginx_confd volume Docker also seeds this file into the volume, so it
# must be gone from the image itself.
RUN rm -f /etc/nginx/conf.d/default.conf

# CrowdSec bouncer config template + the MegooPM lua handlers.
COPY crowdsec-bouncer.conf /etc/nginx/crowdsec-bouncer.conf.template
COPY lua/ /etc/nginx/lua/
COPY docker-entrypoint.sh /docker-entrypoint.sh
# Strip any CR from the entrypoint before making it executable. If the build
# context was checked out with CRLF line endings (Windows / core.autocrlf=true)
# the `#!/bin/sh` shebang carries a trailing \r, the kernel looks for an
# interpreter literally named `sh\r`, and the container dies at startup with:
#   exec /docker-entrypoint.sh: no such file or directory
# The repo .gitattributes pins *.sh to eol=lf; this mirrors backend/Dockerfile
# as defence-in-depth so an already-CRLF checkout still builds a working image.
RUN sed -i 's/\r$//' /docker-entrypoint.sh \
    && chmod +x /docker-entrypoint.sh

EXPOSE 80 443
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["openresty", "-p", "/usr/local/openresty/nginx", "-c", "/etc/nginx/nginx.conf", "-g", "daemon off;"]
