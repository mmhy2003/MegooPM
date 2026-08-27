# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: build tools for asyncpg wheels are usually unnecessary on slim,
# but curl is handy for container healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI only (no daemon) — the Celery worker shells out to
# `docker exec <nginx> openresty -t/-s reload` to validate+reload the managed
# proxy across containers in the dev compose stack (defect 5c770e72 D4). The
# static client binary is copied from the official image so we don't pull the
# whole docker.io package. Inert for the API service, which never reloads nginx;
# production replaces this channel with the MEG-28 worker↔nginx transport.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

# Install Python dependencies first for better layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install .

# Copy application source.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY entrypoint.sh ./entrypoint.sh
COPY worker-entrypoint.sh ./worker-entrypoint.sh
# Strip any CR from the entrypoints before making them executable. If the build
# context was checked out on Windows (or with git core.autocrlf=true) the `bash`
# shebang carries a trailing \r, so the kernel looks for the interpreter `bash\r`
# and the container dies at startup with:
#   env: 'bash\r': No such file or directory
# The repo .gitattributes pins *.sh to eol=lf; this is defence-in-depth so an
# already-CRLF checkout still builds a working image.
RUN sed -i 's/\r$//' ./entrypoint.sh ./worker-entrypoint.sh \
    && chmod +x ./entrypoint.sh ./worker-entrypoint.sh

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
