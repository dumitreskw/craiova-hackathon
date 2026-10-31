# One image for the app (API + built frontend) and the MCP server.
#   docker compose up -d --build        (see docs/09-deployment.md)

# --- frontend --------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Lightpanda (headless browser for fast page reading) ---------------------------
FROM lightpanda/browser@sha256:5d1d724c476957d8c2f8393a51fd089477523b0e5aa9c783c48688785d934fb9 AS lightpanda

# --- backend ---------------------------------------------------------------------
# trixie: Lightpanda's binary is built against Debian 13's glibc.
FROM python:3.12-slim-trixie AS app
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

# Field tools from the Kali workflow, same versions as the Kali VM.
ARG SUBFINDER_VERSION=2.16.0
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates     && curl -fsSL -o /tmp/subfinder.zip        "https://github.com/projectdiscovery/subfinder/releases/download/v${SUBFINDER_VERSION}/subfinder_${SUBFINDER_VERSION}_linux_amd64.zip"     && unzip -o /tmp/subfinder.zip subfinder -d /usr/local/bin && rm /tmp/subfinder.zip     && apt-get purge -y unzip && rm -rf /var/lib/apt/lists/*
ENV UV_TOOL_DIR=/opt/tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install "dnstwist==20250130" --with dnspython     && uv tool install "holehe==1.61"     && uv tool install "maigret==0.6.6"
COPY --from=lightpanda /bin/lightpanda /usr/local/bin/lightpanda
ENV LIGHTPANDA_DISABLE_TELEMETRY=true

COPY backend/ ./
COPY --from=frontend /src/frontend/dist /srv/frontend

# Build id (first 8 chars of the commit, shown in the UI and /api/v1/health):
# the SOURCE_COMMIT build arg when the builder passes one (Coolify can), else
# read from the .git refs that .dockerignore lets into the context.
ARG SOURCE_COMMIT=""
RUN --mount=type=bind,target=/ctx \
    sh /ctx/docker/build-id.sh "$SOURCE_COMMIT" /ctx/.git > BUILD && cat BUILD

# A real home: subfinder and Lightpanda keep their config/state there.
RUN useradd --system --uid 10001 --create-home --home-dir /home/osint osint \
    && mkdir -p /data && chown osint /data
ENV HOME=/home/osint
USER osint
VOLUME ["/data"]
EXPOSE 8000 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
