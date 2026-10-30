# Internal gateway for the Coolify variant: Caddy with the config baked in
# (Coolify runs compose stacks without the repo checkout, so no bind mounts).
FROM caddy:2.8.4
COPY Caddyfile.coolify /etc/caddy/Caddyfile
