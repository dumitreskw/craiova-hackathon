# SearXNG with the platform's settings baked in (see searxng/settings.yml).
FROM searxng/searxng:2026.10.30-ffa88e3ba
COPY searxng/settings.yml /etc/searxng/settings.yml
