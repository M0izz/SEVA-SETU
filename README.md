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

Run the complete test suite (27 tests covering citizen flows, officer flows, integration, and security):

```bash
python -m pytest tests/ -v
```

All 27 test cases pass deterministically.

---

## Evaluation Framework & Dashboard

### Invented Personas & Scenarios
The evaluation harness (`evals/run_evals.py`) runs real MCP client conversations against live endpoints and verifies PostgreSQL assertions:
1. **Rima Bora (70, Sonari):** Eligible fresh applicant assisted by grandson. Verified: PENDING status, correct bank details, age proof stored, SUBMIT audit log.
2. **Anil Das (63, Namti):** Re-applying after previous rejection. Verified: Old REJECTED preserved, new PENDING created.
3. **Sunita Karmakar (58, Sonari):** Underage applicant. Verified: Backend 60+ rule rejects submission; zero database rows created.
4. **Pratima Devi (72, Borgaon):** Non-pilot block applicant. Verified: Refused with redirection to block office; no application created.
5. **Girish Hussain (63, Rajapara):** Forgetful applicant who already has an APPROVED pension. Verified: Duplicate submission prevented.
6. **Iman Gogoi (66, Namti):** Applicant with incorrect bank details. Verified: Existing pending application withdrawn; new application with corrected details created.
7. **Bapu Kaivartta (61, Sonari):** Malformed IFSC on crumpled paper. Verified: Catch-and-correct via `dry_run=true`; only valid IFSC stored.
8. **Jahanara Baruah (82, Khelua):** Applicant whose application exceeded 15-day SLA. Verified: Deemed approval status confirmed.
9. **Officer Approval (Sonari):** Officer approves pending application with recorded reason. Verified: Status updated to APPROVED, `decided_by` set to authenticated officer, reason logged in audit trail.
10. **Officer Double Decision:** Officer attempts to decide an already approved application. Verified: HTTP 409 conflict refused.
11. **Security Isolation:** Citizen token connects to Officer MCP. Verified: Connection rejected with HTTP 403 `insufficient_scope`.

### Running Evals
```bash
python evals/run_evals.py
```
Open `http://localhost:8000/eval-dashboard` to view the rich interactive report with complete transcripts, tool breakdowns, and database checks.

---

## Deliverables Summary

- `DESIGN-NOTE.md`: One-page architectural note on the three critical design decisions and future improvements.
- `LOOM-SCRIPT.md`: Exact 3-minute video walkthrough outline.
- `app/api.py`: Shared service layer and REST API.
- `mcp/`: Dual FastMCP servers with ASGI OAuth 2.1 Bearer guards.
- `evals/`: End-to-end evaluation runner, personas, and mock SMS client.
- `tests/`: Pytest suite with 100% pass rate.
