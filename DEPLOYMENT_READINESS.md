# Seva Setu — Deployment Readiness & Production Operations Guide

**Project:** Seva Setu, Agent-Ready (Old Age Pension Scheme)  
**Track:** Build for Bharat AI Fellowship  
**Date:** October 2026  
**Commit:** `819ffaf`  

---

## 1. System Architecture Overview

The Seva Setu architecture is designed around the principle that the **Flask portal and its PostgreSQL database remain the sole authoritative system of record**. The Model Context Protocol (MCP) servers act as pure OAuth 2.1 resource servers forwarding validated requests to the portal API:

```
[ Public Internet / Clients ]
              │
              ▼
       ┌─────────────┐
       │ Caddy (TLS) │ (Ports 80, 443)
       └──────┬──────┘
              ├───────────────────────────────┬───────────────────────────────┐
              │ /mcp/citizen*                 │ /mcp/officer*                 │ /*
              ▼                               ▼                               ▼
     ┌─────────────────┐             ┌─────────────────┐             ┌─────────────────┐
     │   mcp-citizen   │ (Port 8001) │   mcp-officer   │ (Port 8002) │    Flask App    │ (Port 8000)
     │ (Citizen Tools) │             │ (Officer Tools) │             │ (System of Rec) │
     └────────┬────────┘             └────────┬────────┘             └────────┬────────┘
              │                               │                               │
              └───────────────────────────────┴───────────────────────────────┤
                                                                              ▼
                                                                     ┌─────────────────┐
                                                                     │  PostgreSQL 15  │ (Port 5432)
                                                                     │   (DB of Rec)   │
                                                                     └─────────────────┘
```

### Components:
- **`app`:** Flask application providing the web portal, OAuth 2.1 authorization server, citizen/officer JSON API, and `/eval-dashboard`.
- **`mcp-citizen`:** Dedicated Starlette ASGI server exposing citizen-scoped tools over Streamable HTTP (`/mcp/citizen`).
- **`mcp-officer`:** Dedicated Starlette ASGI server exposing block officer tools over Streamable HTTP (`/mcp/officer`).
- **`db`:** PostgreSQL 15 database enforcing schemas, unique indexes on active applications, and officer block assignments.
- **`scheduler`:** Daily cron container executing `app/scripts/deemed_approval.py` for statutory 15-day SLA enforcement.
- **`smsgw`:** Mock SMS gateway service simulating citizen OTP and approval notifications on the internal network.
- **`caddy`:** Reverse proxy handling automatic HTTPS, domain routing, and RFC 9728 protected-resource discovery endpoints.

---

## 2. Environment Variables Configuration

Copy `.env.example` to `.env` before running in production.

| Variable | Required in Prod | Default (Dev) | Description |
|---|---|---|---|
| `PUBLIC_BASE_URL` | **Yes** | `http://localhost:8000` | Canonical public URL used for OAuth issuer, audience claims, and Caddy |
| `ADMIN_USERNAME` | **Yes** | `admin` | Departmental administrator username |
| `ADMIN_PASSWORD` | **Yes** | `change-me-before-deploying` | Strong administrator password |
| `POSTGRES_DB` | **Yes** | `sewasetu` | Database name |
| `POSTGRES_USER` | **Yes** | `sewasetu` | Database username |
| `POSTGRES_PASSWORD` | **Yes** | `sewasetu123` | Database password (must be rotated in production) |
| `SECRET_KEY` | **Yes** | `ssp-prod-key-2023-donotshare`| Flask session encryption key (must be high-entropy in production) |
| `OFFICER_BLOCK_MAPPINGS`| No | JSON pilot mapping | Dynamic JSON override mapping officer usernames to assigned administrative blocks |
| `ALLOWED_DOC_HOSTS` | No | Empty | Comma-separated domain allowlist for document downloads (must resolve to global IPs in prod) |
| `LLM_API_KEY` | No | Empty | Optional API key for live external LLM evaluation runs (OpenAI / OpenRouter / Anthropic) |
| `LLM_BASE_URL` | No | `https://api.openai.com/v1` | Base URL for OpenAI-compatible LLM endpoint |
| `LLM_MODEL` | No | `gpt-4o-mini` | Model name for evaluation |

---

## 3. Database Initialization and Safe Migrations

The database schema is versioned and managed idempotently via `app/migrations/apply_migrations.py`.

### Fresh Deployment:
Docker Compose automatically initializes the database using files mounted in `/docker-entrypoint-initdb.d/`:
1. `01-schema.sql`: Core tables (`applications`, `portal_users`, `otps`, `audit_log`, `oauth_keys`, `oauth_clients`).
2. `02-seed.sql`: Pilot seed data.
3. `03-migration-001.sql`: Partial unique index preventing duplicate active applications per mobile number.
4. `04-migration-002.sql`: `officer_blocks` table binding officer accounts to assigned administrative blocks.

### Existing Deployment / Upgrades:
Run the standalone migration runner:
```bash
python app/migrations/apply_migrations.py
```
- **Preflight Check:** Migration `001_active_unique` inspects the database for existing duplicate active applications (`PENDING`, `APPROVED`, `DEEMED_APPROVED`). If conflicts are detected, it halts without altering or deleting any citizen records, outputting actionable mobile numbers.
- **Transaction Rollback:** Each migration is executed inside a transaction. In the event of an error, `conn.rollback()` is invoked to restore the database to a clean state.
- **Idempotency:** Applied versions are recorded in `schema_migrations` and will not re-execute.

---

## 4. Health Checks and Process Management

- **PostgreSQL (`db`):** Uses native `pg_isready -U sewasetu` with 5s interval, 5s timeout, and 5 retries.
- **Dependent Services (`app`, `scheduler`):** Configure `condition: service_healthy` on `db` to guarantee that schema and migrations are available before application startup.
- **Container Restarts:** All services are configured with `restart: unless-stopped`.

---

## 5. Startup Commands

### Local Development / Evaluation:
```bash
# 1. Start all containers
docker compose up -d

# 2. Run test suite
python -m pytest tests/ --cov=app --cov=mcp -v

# 3. Run evaluation harness
python evals/run_evals.py

# 4. View dashboard
open http://localhost:8000/eval-dashboard
```

### Production Deployment (e.g. Ubuntu 22.04 / 24.04 LTS VPS):
```bash
# 1. Clone repository to server
git clone <repository_url> /opt/sewasetu
cd /opt/sewasetu

# 2. Configure production secrets
cp .env.example .env
nano .env

# 3. Set domain in Caddy and environment
# e.g. PUBLIC_BASE_URL=https://sewasetu.assam.gov.in

# 4. Launch production stack with automatic HTTPS
docker compose -f docker-compose.yml up -d --build

# 5. Verify service status
docker compose ps
docker compose logs -f caddy
```

---

## 6. Deployment Verification Checklist

### Verified Locally:
- [x] Full test suite (97 tests) passing with 54% statement coverage.
- [x] Dual MCP servers running with strictly separated tool registries.
- [x] Real MCP client connected and exercised over Streamable HTTP transport.
- [x] Block-level officer authorization enforced across all officer API endpoints.
- [x] SSRF transport-level IP pinning active, rejecting loopback, metadata, CGNAT, and unusual IP representations.
- [x] Statutory 15-day deemed approval logic functioning atomically.
- [x] Evaluation dashboard (`/eval-dashboard`) operational with PII masking and honest mode disclosures.
- [x] Clean Git working tree at tested commit `819ffaf`.

### Remaining Manual Steps for Hosting Provider / Cloud Deployment:
- [ ] **DNS Record:** Point public domain (A/AAAA records) to the server's public IP address.
- [ ] **Firewall Ports:** Ensure inbound ports `80` (HTTP) and `443` (HTTPS) are open on the firewall.
- [ ] **Secret Generation:** Replace default passwords in `.env` with random cryptographic secrets (`openssl rand -hex 32`).
- [ ] **Production Run:** Execute `docker compose up -d` on the live server and confirm Caddy obtains a valid Let's Encrypt TLS certificate.
- [ ] **Live LLM Evaluation (Optional):** Provide `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` to run live model evals via `python evals/run_evals.py`.
