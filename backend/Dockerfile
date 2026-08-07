# Pinned to the 3.13 toolchain target (local dev is on 3.14, but CI/prod pin 3.13 until every
# C-extension dependency ships 3.14 wheels). Digest-free tag pin keeps this readable; a release
# pipeline can pin the digest.
FROM python:3.13-slim AS builder

# uv is the sole packaging tool; copy the static binary from its official distroless image rather
# than curl-piping an installer, so the build is reproducible and network-light.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The dependency graph is resolved from the committed lockfile only (--frozen); the project itself
# is an editable install over src/, so the source tree is copied before sync. --no-dev keeps the
# lint/type/test toolchain out of the runtime venv.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --all-extras


FROM python:3.13-slim AS runtime

# Non-root runtime user — nothing in the container needs root, and a compromised process shouldn't
# hold it.
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app

# The resolved virtualenv and the application source, owned by the unprivileged user.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src/ ./src/
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# Flat top-level packages live under src/ (agent, api, scheduler, ...); put the venv first and src
# on the path so `uvicorn api.app:app` and `python -m scheduler` both resolve. yfinance writes a
# tz cache — point it at a writable in-image path since the default location can be read-only.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    YF_TZ_CACHE_DIR="/app/.cache/yf_tz" \
    SERVICE_ROLE=api \
    PORT=8000

RUN mkdir -p /app/.cache/yf_tz /app/data && chown -R app:app /app/.cache /app/data

USER app

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
