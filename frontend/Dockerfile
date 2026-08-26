# syntax=docker/dockerfile:1
# Development image for the MegooPM frontend: runs `next dev` inside the
# compose stack. Production builds are a separate concern (later ticket); this
# image is optimized for a working local dev loop, not a minimal runtime.
FROM node:22-alpine

ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY package.json package-lock.json ./
RUN npm ci

# Copy the rest of the source.
COPY . .

EXPOSE 3000

# Bind to 0.0.0.0 so the dev server is reachable from the host / other
# containers, not just localhost inside the container.
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]
