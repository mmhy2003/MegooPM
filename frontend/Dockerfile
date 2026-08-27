# syntax=docker/dockerfile:1
# MegooPM frontend image — two targets:
#   dev     `next dev` with the source bind-mounted (docker-compose.dev.yml)
#   runner  Next.js standalone server (docker-compose.yml / docker-compose.ha.yml)
#
# NEXT_PUBLIC_* values are inlined at BUILD time by Next, so the production
# targets take them as build args (compose passes them from .env). Changing
# NEXT_PUBLIC_API_BASE_URL therefore needs `docker compose build frontend`.

FROM node:22-alpine AS deps
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- Development: hot reload, source arrives via bind mount ---------------
FROM deps AS dev
ENV NODE_ENV=development
COPY . .
EXPOSE 3000
# Bind to 0.0.0.0 so the dev server is reachable from the host / other
# containers, not just localhost inside the container.
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

# --- Production build -----------------------------------------------------
FROM deps AS builder
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_AUTH_ENABLED=false
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_PUBLIC_AUTH_ENABLED=${NEXT_PUBLIC_AUTH_ENABLED} \
    NODE_ENV=production
COPY . .
RUN npm run build

# --- Production runtime: standalone server, non-root ----------------------
FROM node:22-alpine AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
