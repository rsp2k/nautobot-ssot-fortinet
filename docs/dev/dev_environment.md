# Development Environment

The repo ships a Docker-based dev stack that brings up Nautobot + Postgres + Redis + Celery worker, with the plugin source bind-mounted for hot-reload.

## Prerequisites

- Docker + Docker Compose
- An external `caddy` Docker network (for reverse-proxy of the dev UI)
- A FortiGate to test against (optional but recommended — fixture-based testing covers most paths but the live e2e harnesses catch real-world quirks)

## Bring up the stack

```bash
cd development
cp .env.example .env
# edit .env — at minimum set NAUTOBOT_SECRET_KEY
make up
# wait ~60s for first-boot migrations
make seed
# browse https://ssot-fortinet-dev.local/
```

The seed creates an `ExternalIntegration` named `fgt-dev` with placeholder credentials. To talk to a real FortiGate, set `FGT_DEV_URL` + `FGT_DEV_TOKEN` (or `FGT_DEV_USERNAME` + `FGT_DEV_PASSWORD`) in `development/.env` and re-run `make seed`.

## Daily workflow

| Task | Command |
|---|---|
| Tail Nautobot logs | `make logs-web` |
| Restart web after src/ changes | `make restart` |
| Drop into a shell on the web container | `make shell` |
| Open a Django shell with all models pre-imported | `make nbshell` |
| Re-seed the dev fixture data | `make seed` |
| Run the firewall fixture e2e (no FortiGate needed) | `make e2e-firewall` |
| Run the wireless fixture e2e (no FortiGate needed) | `make e2e-wireless` |
| Run the firewall live e2e (real FortiGate) | `make e2e-live-firewall` |
| Run the wireless live e2e (real FortiGate) | `make e2e-live-wireless` |
| Validate the push direction round-trip | `make e2e-push-validate` |
| Wipe everything (DESTRUCTIVE — drops volumes) | `make clean` |

## Testing

Unit tests run inside the container (where all deps are installed):

```bash
docker compose exec --workdir /opt/plugin nautobot-web pytest -q
```

Expected output: `174 passed in 0.5s` (as of v2026.05.18).

The unit suite uses `MagicMock`-stubbed Django (see `tests/conftest.py`) so tests don't require a running Nautobot. The integration coverage is provided by the e2e harnesses listed above.

## Linting

Ruff config lives in `pyproject.toml`:

```bash
# from the repo root, on the host (uv tool install ruff if needed)
ruff check src/ tests/
ruff format src/ tests/
```

Per-file ignores for the Nautobot-side CRUD modules silence `D102`/`D107`/`D417` since those methods follow a documented pattern at the class level.

## Caddy network

The dev compose attaches `nautobot-web` to an external `caddy` network for HTTPS reverse-proxy at the configured `DOMAIN`. If you don't run [`caddy-docker-proxy`](https://github.com/lucaslorentz/caddy-docker-proxy), either set one up or modify `development/docker-compose.yml` to expose port 8080 directly.

## Multiple parallel dev stacks

The compose project is named via `COMPOSE_PROJECT_NAME` in `.env` (default `ssot-fortinet-dev`). Each Nautobot service uses **project-prefixed** hostnames (`ssot-fortinet-db`, `ssot-fortinet-cache`) to avoid DNS collisions with other Nautobot stacks on the same Docker host. Change `COMPOSE_PROJECT_NAME` to run a second isolated stack from the same checkout.

## See also

- [Contributing](contributing.md) — how to submit changes
- [Extending the App](extending.md) — common extension patterns
