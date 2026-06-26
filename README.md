# Marketing Agent

AI marketing assistant that plans, writes, and schedules social media content.  
Built on FastAPI + PostgreSQL + Postiz (self-hosted). Humans approve everything before it goes live.

## Architecture

```
FastAPI Agent Layer  →  Postiz REST API  →  Social Platforms
                     ↑
         Human approval gate (always required for publishing)
```

- **Strategy Agent** (claude-sonnet-4-6): brand brief → 7-day content plan
- **Content Agent** (claude-sonnet-4-6): plan → posts
- **Critic Agent** (claude-haiku-4-5-20251001): review tone, brand fit, errors
- **Analytics Agent** (claude-haiku-4-5-20251001): metrics → next week's adjustments

## Quick Start (local)

```bash
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, POSTIZ_JWT_SECRET
make dev-build

# Health check
curl http://localhost:8001/api/health
```

**Service ports:**

| Service | Port | Purpose |
|---|---|---|
| FastAPI app | 8001 | Our API (host 8001 → container 8000) |
| Postiz frontend | 5000 | Postiz UI |
| Postiz API | 5000/api | Postiz REST API — nginx routes `/api/` → NestJS (`/api/public/v1/...`) |
| Our Postgres | 5432 | App database |

Port 8001 is used on the host because port 8000 was in use on this machine.

## Postiz API key setup

After `make dev-build`:

1. Open [http://localhost:5000](http://localhost:5000) — create an account
2. Settings → Developer → API Keys → Create Key
3. Copy the key → set `POSTIZ_API_KEY=<key>` in `.env`
4. `docker compose restart app`

## Running tests

```bash
# In Docker (matches CI)
make test

# Locally (faster)
make test-local
```

## Database migrations

```bash
make migrate                   # apply all pending
make migrate-create            # generate new migration
make migrate-down              # revert last migration
```

## GCP / production deployment

```bash
cp .env.example .env
# Fill in DOMAIN, CERTBOT_EMAIL, all secrets

make dev-build

# Point DNS A record to your VM IP, then:
make ssl-init DOMAIN=yourdomain.com CERTBOT_EMAIL=you@example.com
```

`make ssl-init` renders the nginx template (substituting only `${DOMAIN}` via
`envsubst '${DOMAIN}'` so nginx variables like `$http_upgrade` are preserved),
obtains a Let's Encrypt certificate, and starts the nginx+certbot stack.
Renewal runs automatically via the certbot loop.

## Postiz rate limits

The self-hosted Postiz public API rate limit is configurable (see Postiz env).
The `PostizClient` retries automatically on 429 with exponential backoff.
Each distinct post requires a separate `POST /public/v1/posts` request — plan
scheduling to stay within your configured limit.

## TODO — Phase 4 analytics

The Postiz public API has no analytics endpoint. Phase 4 analytics options:
- Read post `state` / `releaseURL` from `GET /public/v1/posts`
- Pull metrics from platform-native APIs (X, LinkedIn, etc.)
- Add a third-party analytics layer

Decision deferred to Phase 4.

## Build phases

| Phase | Status | What |
|---|---|---|
| 1 — Foundation | **Done** | FastAPI + Docker + Postiz + PostizClient |
| 2 — First agent | Pending | Models + brand endpoints + draft generation |
| 3 — Approval gate | Pending | Status machine + approve/reject/schedule |
| 4 — The loop | Pending | Analytics → feed next plan |
