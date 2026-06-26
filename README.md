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
| 2 — First agent | **Done** | Gemini + LangGraph + brand endpoints + draft generation |
| 3 — Approval gate | Pending | Status machine + approve/reject/schedule |
| 4 — The loop | Pending | Analytics → feed next plan |

## LangGraph generation flow (Phase 2)

```
POST /plans:generate
        │
        ▼
  strategy_node  ──────────────────────────────────────────────┐
  (gemini-2.5-pro)                                             │
  → 7 ContentIdeas                                             │
        │                                                      │
        ▼  (repeat for each idea)                              │
  content_node                                                 │
  (gemini-2.5-pro)                                             │
  → ContentOutput {content, hashtags, suggested_time}         │
        │                                                      │
        ▼                                                      │
  critic_node                                                  │
  (gemini-2.5-flash)                                           │
  → CriticOutput {approved, issues, fixed_body?}               │
        │                                                      │
        ├─ not approved + revision_count == 0 ──► apply        │
        │   fixed_body, set revision_count=1 → content_node   │
        │                                                      │
        └─ approved (or revision exhausted) ──► append post   │
                                                               │
  advance_node: current_idx++ → next idea or END ─────────────┘
```

All agents use `with_structured_output(Schema, method="json_schema")` — no
free-form JSON parsing. Nodes are DB-pure: they accumulate `action_logs` in
state; `run_generation` writes all logs + Post rows in one transaction.

**Model tiers:**
- `gemini-2.5-pro` (`REASONING_MODEL`) — strategy + content (reasoning-heavy)
- `gemini-2.5-flash` (`CHEAP_MODEL`) — critic (fast + cheap review)

**MemorySaver checkpointer** — in-memory only. Phase 3 will swap to a Postgres
checkpointer so paused human-in-the-loop runs survive restarts.

## Enabling LangSmith tracing

```bash
# In .env:
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-key-here
LANGSMITH_PROJECT=marketing-agent
```

When enabled, every `graph.ainvoke` call produces a trace at smith.langchain.com.
Leave `LANGSMITH_TRACING=false` (default) for local dev and all tests.

## Phase 2 API

```
POST /api/workspaces                      — create workspace
GET  /api/workspaces/{id}                 — fetch workspace
PUT  /api/workspaces/{id}/brand           — upsert brand profile
GET  /api/workspaces/{id}/brand           — fetch brand profile
POST /api/workspaces/{id}/plans:generate  — start generation (202, background)
GET  /api/workspaces/{id}/plans/{plan_id} — poll status + posts
```

Posts land in `pending_approval` — **nothing is published**. Phase 3 adds the
approve / reject / schedule workflow and Postiz integration.
