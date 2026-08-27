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

# No Docker CLI here: the worker validates/reloads the managed nginx through
# the token-gated reload agent in the nginx container (scripts/nginx_remote.py,
# docs/nginx-engine.md), never through the Docker socket.

# Install Python dependencies first for better layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install .

# Copy application source. `scripts/` ships too: the worker's nginx reload
# transport (`python -m scripts.nginx_remote`) and the operator CLIs
# (`scripts.create_user`, `scripts.export_openapi`) run from the image.
COPY app ./app
COPY scripts ./scripts
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
