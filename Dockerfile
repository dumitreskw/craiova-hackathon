# One image for the app (API + built frontend) and the MCP server.
#   docker compose up -d --build        (see docs/09-deployment.md)

# --- frontend --------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- backend ---------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    OSINT_DATA_DIR=/data \
    OSINT_FRONTEND_DIR=/srv/frontend

WORKDIR /srv/backend
# Dependencies first (cached layer), then the code.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --extra evidence --no-install-project
# Chromium + its system libraries, for screenshot evidence.
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

COPY backend/ ./
COPY --from=frontend /src/frontend/dist /srv/frontend

RUN useradd --system --uid 10001 --home /srv osint \
    && mkdir -p /data && chown osint /data
USER osint
VOLUME ["/data"]
EXPOSE 8000 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
