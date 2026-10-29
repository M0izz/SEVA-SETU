# Sewa Setu — Old Age Pension Portal (Agent-Ready)

**Department of Social Welfare, Government of Purvanchal**  
*Build for Bharat AI Fellowship — System Implementation*

---

## Overview

Sewa Setu is the official government welfare portal managing the Old Age Pension Scheme (Rs. 250/month for citizens aged 60 and above) under the Purvanchal Right to Public Services (RTPS) Act.

This project transforms the existing Flask-based portal into an **agent-ready public service** via two independent **Model Context Protocol (MCP)** servers over **Streamable HTTP**, allowing AI assistants to act on behalf of authenticated citizens and block officers without compromising security, accountability, or the portal's authority as the single system of record.

---

## Key System Endpoints

- **Portal & Web UI:** `http://localhost:8000`
- **Citizen Status Portal:** `http://localhost:8000/status` (Mobile + DOB as `DDMMYYYY`)
- **Departmental Admin:** `http://localhost:8000/admin` (User: `admin`, Password: `ADMIN_PASSWORD`)
- **Simulated SMS Gateway:** `http://localhost:8000/__gateway/`
- **Citizen MCP Server:** `http://localhost:8000/mcp/citizen` (or direct `:8001/mcp/citizen`)
- **Officer MCP Server:** `http://localhost:8000/mcp/officer` (or direct `:8002/mcp/officer`)
- **Evaluation Dashboard:** `http://localhost:8000/eval-dashboard`
- **OAuth Discovery:** `http://localhost:8000/.well-known/oauth-authorization-server`
- **JWKS Keyset:** `http://localhost:8000/.well-known/jwks.json`

---

## Architectural Highlights

### 1. Dual Independent MCP Servers (Streamable HTTP)
- **Separate Registries:** The citizen MCP (`mcp/mcp_citizen.py`) never exposes officer tools. The officer MCP (`mcp/mcp_officer.py`) never exposes citizen submission tools.
- **Official Python MCP SDK:** Implemented using `FastMCP` from the official `mcp` SDK configured with `stateless_http=True` and Streamable HTTP endpoints.

### 2. OAuth 2.1 RFC 9728 Resource Servers
- **Zero Trust Authentication:** MCP servers never accept raw caller-supplied IDs. Both servers act as OAuth 2.1 Resource Servers.
- **Cryptographic Token Verification:** Tokens are RS256 JWTs verified using the portal's public JWKS.
- **RFC 8707 Resource Indicators:** Tokens are audience-bound (`$PUBLIC_BASE_URL/mcp/citizen` vs `$PUBLIC_BASE_URL/mcp/officer`). A citizen token presented to the officer MCP is cryptographically rejected at the ASGI middleware level.
- **Short Absolute Sessions:** Citizen sessions terminate after 2 hours; officer sessions terminate after 8 hours. Tokens cannot be refreshed indefinitely on shared devices.

### 3. Shared Service Layer & Zero Duplicated Logic
- All operations pass through `app/api.py` and invoke the portal's authoritative functions:
  - `validate_application()`: Enforces minimum age of 60, DD/MM/YYYY date format, valid block, 9–18 digit bank account, 11-char IFSC, mandatory age-proof document, and scheme deadline.
  - `active_application()`: Prevents duplicate active applications.
  - `create_application()`: Inserts into PostgreSQL and sets up status portal accounts atomically.
  - `write_audit()`: Writes immutable audit logs for all submissions, withdrawals, and decisions.
  - `_admin_decide()`: Validates that only pending applications can be decided, requiring an accountable rationale.
- **Pilot Blocks Restriction:** As directed, agent submissions are restricted to pilot blocks (`Sonari` and `Namti`). Applicants from other blocks are redirected to block offices or the main portal.

---

## MCP Tools Reference

### Citizen MCP (`/mcp/citizen`)
| Tool | Description |
| :--- | :--- |
| `get_scheme_info` | Retrieves official scheme facts, benefit rate, SLA, and pilot blocks. |
| `check_eligibility` | Read-only check for age (60+), window status, and active application state. |
| `get_required_documents` | Retrieves accepted age-proof document types and specifications (max 5MB). |
| `apply_for_pension` | Validates (with `dry_run=true`) or submits a completed pension application. |
| `get_my_application` | Inspects current application status, SLA countdown, and decision history. |
| `withdraw_my_application` | Withdraws an existing pending application to allow re-application with corrected details. |

### Officer MCP (`/mcp/officer`)
| Tool | Description |
| :--- | :--- |
| `queue_summary` | Provides application counts by status, overdue pending cases, and block counts. |
| `list_applications` | Lists queue applications (paginated) with optional status, block, or mobile filters. |
| `get_application` | Inspects full details of an application including age today, documents, and audit log. |
| `decide_application` | Records an `APPROVED` or `REJECTED` decision with a mandatory justification. |

---

## Running with Docker Compose

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Start the full stack:
   ```bash
   ADMIN_PASSWORD=sewa-admin-2026 docker compose up --build -d
   ```

3. Services started:
   - `db`: PostgreSQL 15 with ~100k seeded application records
   - `app`: Authoritative Flask portal, OAuth 2.1 AS, and `/eval-dashboard`
   - `scheduler`: Nightly cron for 15-day statutory deemed approvals
   - `smsgw`: Simulated telecom SMS gateway
   - `mcp-citizen`: Citizen MCP server on port 8001
   - `mcp-officer`: Officer MCP server on port 8002
   - `caddy`: TLS reverse proxy routing all endpoints under a single public base URL

---

## Running Automated Tests

Run the complete test suite (56 automated tests covering citizen flows, officer flows, real MCP integration over Streamable HTTP, OAuth token verification, concurrency defense, SSRF elimination, document magic byte validation, and dashboard privacy):

```bash
python -m pytest tests/ --cov=app --cov=mcp -v
```

All 56 test cases pass deterministically.

---

## Security Hardening & Vulnerability Remediation

This release includes complete remediation of all audit findings and security hardening:

1. **Dual MCP SDK Compatibility (`mcp 1.x` and `2.x`):**
   - Automatically supports both FastMCP (`mcp 1.x`) and MCPServer (`mcp 2.x`).
   - Configures `TransportSecuritySettings` to operate correctly behind reverse proxies (Caddy) and containerized networks.
   - Distinct endpoints `/mcp/citizen` and `/mcp/officer` with zero cross-tool leakage.

2. **SSRF Elimination (`app/api.py`):**
   - Direct base64 content strictly validated with byte limits before decoding.
   - Remote URL validation rejects loopback, private RFC 1918 subnets, cloud metadata (`169.254.169.254`), internal Docker service names (`smsgw`, `db`, `app`, `scheduler`), IPv6 loopback, non-standard ports, and embedded credentials.
   - Disables HTTP redirects (`allow_redirects=False`) to prevent open-redirect SSRF bypass.
   - Enforces 5-second timeouts and bounded streaming downloads (max 5 MB).

3. **File Signature & Magic Bytes Validation:**
   - Every uploaded or fetched document is inspected for authentic magic bytes (`%PDF-`, `\xff\xd8\xff`, `\x89PNG\r\n\x1a\n`).
   - Rejects HTML, SVG, XML, PHP, scripts, DOS/PE binaries, and polyglots.
   - Prevents path traversal using sanitized filenames and isolated upload directory storage.

4. **Concurrency Hardening & Duplicate Prevention:**
   - Partial unique database index: `CREATE UNIQUE INDEX idx_active_app_mobile ON applications(mobile) WHERE status IN ('PENDING', 'APPROVED', 'DEEMED_APPROVED')`.
   - Handles race conditions gracefully with HTTP 409 `active_application_exists`.
   - Preserves legitimate reapplication after rejection or withdrawal.
   - Includes repeatable migration in `app/migrations/001_active_unique.sql` and `app/migrations/apply_migrations.py`.

5. **PII Masking & Dashboard Privacy:**
   - Public evaluation dashboard automatically sanitizes mobile numbers (`98640XXXXX`), bank accounts (`31XXXXXX5678`), OTPs, and access tokens from both rendered HTML and page source.

---

## Known Limitations

- **Officer Block Jurisdiction:** The legacy portal database schema provides a single administrator account (`admin`) rather than individual officer accounts with assigned administrative blocks. In the current design, the `block` parameter in officer tools acts as a queue filter rather than an authorization boundary. True officer role-based access control (RBAC) requires expanding the schema with dedicated officer identity and jurisdiction tables.
- **LLM vs Scripted Evals:** The evaluation harness defaults to deterministic scripted MCP client execution for reproducible CI/CD testing. A live LLM mode can be enabled by supplying `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`.

---

## Deliverables Summary

- `DESIGN-NOTE.md`: One-page architectural note on critical decisions and statutory workflows.
- `LOOM-SCRIPT.md`: Video walkthrough outline.
- `app/api.py`: Hardened shared service layer and REST API.
- `app/migrations/`: Migration scripts and pre-flight conflict checks.
- `mcp/`: Dual-compatible MCP servers with OAuth 2.1 Bearer guards.
- `evals/`: End-to-end evaluation runner with PII sanitization and real Postgres checks.
- `tests/`: 56 automated pytest test cases with 100% pass rate.

