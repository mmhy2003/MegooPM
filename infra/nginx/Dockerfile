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

# Install the CrowdSec OpenResty bouncer module (+ its lua-resty deps) via opm.
# Pinned so the image is reproducible and the lua API our handlers call is stable.
RUN opm get crowdsecurity/lua-cs-bouncer=1.0.8 \
    && opm get openresty/lua-resty-http \
    && opm get openresty/lua-resty-string

# OpenResty keeps mime.types under its own prefix; the base nginx.conf includes
# /etc/nginx/mime.types (the vanilla-nginx path), so bridge it.
RUN mkdir -p /etc/nginx \
    && ln -sf /usr/local/openresty/nginx/conf/mime.types /etc/nginx/mime.types

# CrowdSec bouncer config template + the MegooPM lua handlers.
COPY crowdsec-bouncer.conf /etc/nginx/crowdsec-bouncer.conf.template
COPY lua/ /etc/nginx/lua/
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80 443
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["openresty", "-p", "/usr/local/openresty/nginx", "-c", "/etc/nginx/nginx.conf", "-g", "daemon off;"]
