# YOSAKI VPS deployment checklist

This deployment layout runs the FastAPI backend and serves the built Vue app through Nginx. It intentionally binds the HTTP port to `127.0.0.1:8080`; put a host-level HTTPS reverse proxy (Caddy/Nginx/Alibaba Cloud certificate proxy) in front of it.

## Before deployment

1. Use a fresh VPS directory and copy the repository without `data/yosaki.sqlite3`, `data/.secret_key`, `.venv`, or `node_modules`.
2. Install Docker Engine and Docker Compose plugin on the VPS. Docker is not installed in the current Windows development environment, so image builds are not locally verified.
3. Copy `deploy/.env.production.example` to `.env.production` and set:
   - `YOSAKI_ALLOWED_ORIGINS=https://your-real-domain.example`
   - `YOSAKI_COOKIE_SECURE=1`
   - a persistent Fernet `YOSAKI_SECRET_KEY`
4. Place TLS termination in front of `127.0.0.1:8080`; do not expose port 8000 or the Docker HTTP port directly to the Internet.
5. Open only 80/443 (and SSH with key authentication) in the VPS firewall.

## Start

```bash
docker compose -f deploy/docker-compose.production.yml up -d --build
```

Health check from the VPS:

```bash
curl -i http://127.0.0.1:8080/health
```

Then open the HTTPS domain, create the single owner account, and configure model/MCP secrets through the authenticated UI.

## Updates and rollback

```bash
docker compose -f deploy/docker-compose.production.yml pull
docker compose -f deploy/docker-compose.production.yml up -d --build
docker compose -f deploy/docker-compose.production.yml logs --tail=100 backend
```

Keep the named `yosaki_data` volume. Before upgrades, export an encrypted `.ybackup` from System Settings and copy it off the VPS. A restore rehearsal is required before treating the deployment as production-ready.

## Security boundaries

- Do not put passwords, model API keys, MCP keys, or `.env.production` in Git.
- Keep the owner account password and encrypted backup password separate.
- Verify HTTPS, secure cookies, origin allow-list, firewall rules, restart persistence, model calls, MCP tool confirmation, and backup restore from a second device.
- This is a deployment scaffold, not proof that the current VPS is reachable or that any provider/MCP endpoint is healthy.
