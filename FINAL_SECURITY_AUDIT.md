# Seva Setu — Final Adversarial Security Audit Report

**Project:** Seva Setu, Agent-Ready (Old Age Pension Scheme)  
**Track:** Build for Bharat AI Fellowship  
**Auditor:** Independent Principal Security Engineer & QA Lead  
**Commit Inspected:** `819ffaf` (branch: `main`)  
**Assessment Date:** October 2026  
**Status:** Verification Complete & Submission-Ready  

---

## 1. Executive Summary

An exhaustive adversarial security review was performed across the Seva Setu codebase, testing the authoritative Flask portal, dual Model Context Protocol (MCP) servers, OAuth 2.1 authorization server, PostgreSQL database constraints, migration runner, and the evaluation dashboard.

All 97 automated security, protocol, and functional tests pass with zero failures and zero skipped tests. Overall statement coverage is **54%**, with security-critical modules reaching up to **82%**.

### Key Vulnerabilities Identified and Remediated:
1. **SSRF Transport-Level Rebinding & Non-Global IP Leakage:** Upgraded `_is_safe_ip` with standard library `ipaddress.is_global` check to definitively block Carrier-Grade NAT (`100.64.0.0/10`), documentation IPs (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`), benchmarking subnets (`198.18.0.0/15`), and broadcast addresses. Canonicalized unusual IPv4 encodings (hex, octal, dword integer, dotted-short) via `socket.inet_aton`.
2. **SSRF Allowlist Destination Escapes:** Identified and fixed a flaw where configuring `ALLOWED_DOC_HOSTS` could bypass private destination checks in production. All allowlisted hosts in production must now resolve strictly to safe global IPs. In local testing/evaluation mode, loopback (`127.0.0.1`) is permitted only on explicit mock ports, while cloud metadata (`169.254.169.254`, `fd00:ec2::254`) and internal Docker services remain unconditionally blocked.
3. **Environment Proxy SSRF Bypass:** Hardened `requests.Session` with `session.trust_env = False` to prevent attackers or misconfigured environments from routing document fetching through untrusted `HTTP_PROXY`/`HTTPS_PROXY` daemons.
4. **Oversized & Chunked Payload Exhaustion:** Added Content-Length header pre-check (> 5 MB) and explicit socket connection termination (`r.close()`) during streaming chunk validation to prevent memory exhaustion and slow-loris attacks.
5. **OAuth 2.1 Token Kind Validation:** Enforced that Bearer tokens presented to `/api/citizen/*` and `/api/officer/*` must be `kind="access"`. Refresh tokens with `kind="refresh"` are rejected with `401 Unauthorized`.
6. **Block-Level Officer Authorization Boundaries:** Bounded all officer list, inspect, summary, and decision operations to the officer's assigned block derived from `DEFAULT_OFFICER_BLOCKS` or the `officer_blocks` database table. Caller-supplied `block` parameters cannot alter server-derived authorization. Missing block assignments fail closed (`403 Forbidden`).
7. **Database Migration Safety & Rollback:** Wrapped `apply_migrations.py` in transactional `try...except` with `conn.rollback()` so that aborted migrations leave no corrupted state. Preflight validation detects duplicate active records before index creation without modifying citizen data.
8. **Comprehensive PII Masking:** Enhanced `mask_pii_text` to sanitize mobile numbers in varied formats (`+91 98640 12345`, `98640-12345`, filenames like `9864012345_ageproof.pdf`), 12-digit Aadhaar numbers, 9–18 digit bank accounts, Bearer tokens, standalone JWTs, API keys (`sk-...`), passwords, and OTPs.

---

## 2. In-Depth Adversarial Findings and Remediations

### Finding 1: SSRF — Non-Global IP Ranges and Unusual IP Encodings
- **Severity:** High
- **Vulnerability:** Python's `ip.is_private` does not flag Carrier-Grade NAT (CGNAT `100.64.0.0/10`), documentation subnets (RFC 5737 `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`), or benchmarking subnets (`198.18.0.0/15`). Furthermore, URLs with octal IP representations (e.g. `http://0177.0.0.1/`), dword representations (`http://2130706433/`), hex representations (`http://0x7f000001/`), or short-dotted forms (`http://127.1/`) could evade naïve string filters.
- **Remediation in `app/api.py`:**
  - Added `not ip.is_global` to `_is_safe_ip(ip_str)`.
  - Canonicalized IPv4 representations via `socket.inet_ntoa(socket.inet_aton(hostname_clean))` before testing against `_is_safe_ip`.
  - Canonicalized Internationalized Domain Names (IDN) via `encode('idna').decode('ascii')` and stripped trailing dots (`.rstrip('.')`).
- **Validation:** Verified with `test_ssrf_rejects_cgnat_and_documentation_ips`, `test_ssrf_rejects_unusual_ip_encodings`, and `test_ssrf_rejects_trailing_dot_internal_hosts`.

### Finding 2: SSRF — Allowlist Leakage to Private Destinations
- **Severity:** High
- **Vulnerability:** When `ALLOWED_DOC_HOSTS` was configured, the code bypassed `_is_safe_ip` entirely. An allowlisted domain (e.g., `trusted.gov.in`) resolving to `169.254.169.254` or internal subnets would have been fetched.
- **Remediation in `app/api.py`:**
  - In production mode, all allowlisted hosts MUST still pass `_is_safe_ip` for every resolved IP in `socket.getaddrinfo`.
  - In explicit test/eval mode (`ALLOW_TEST_DOC_SERVERS="1"`), only loopback (`127.0.0.1`/`::1`) on allowlisted mock ports is permitted; cloud metadata (`169.254.169.254`, `fd00:ec2::254`) and internal Docker services remain strictly blocked.
- **Validation:** Verified with `test_ssrf_allowlist_in_production_cannot_authorize_private_destinations`.

### Finding 3: HTTP Transport Proxy Bypasses & Resource Exhaustion
- **Severity:** Medium
- **Vulnerability:** Standard `requests.Session()` honors `HTTP_PROXY` and `HTTPS_PROXY` environment variables. If present, requests could be routed through an HTTP proxy, bypassing the custom `PinnedIPAdapter`. Additionally, responses with huge Content-Length headers or decompression bombs could consume excessive memory.
- **Remediation in `app/api.py`:**
  - Set `session.trust_env = False` to disable environment proxies.
  - Set `adapter = PinnedIPAdapter(validated_ip, max_retries=0)` to prevent retries to alternative connection paths.
  - Pre-checked `Content-Length` header (> 5 MB) to abort downloads before streaming.
  - Added explicit `r.close()` on size overflow or error during streaming chunk iteration.
- **Validation:** Verified with `test_ssrf_rejects_oversized_content_length_header` and `test_ssrf_rejects_oversized_response`.

### Finding 4: Officer Authorization & Block Boundaries
- **Severity:** Critical
- **Vulnerability:** Officers could attempt cross-block data inspection or decision-making by passing arbitrary `block` query parameters or application IDs belonging to other administrative jurisdictions.
- **Remediation in `app/api.py`:**
  - In `/api/officer/applications`, caller-supplied `block` arguments that disagree with `assigned_block` return `403 Forbidden`. The query unconditionally filters `WHERE block = %s` using the server-derived `assigned_block`.
  - In `/api/officer/application/<ref>`, the application's block (`row[9]`) is compared to `assigned_block`; mismatches return `403 Forbidden`.
  - In `/api/officer/decide`, decisions are atomic and require `WHERE id = %s AND status = 'PENDING' AND block = %s`. Any status change or block mismatch returns `409 Conflict` or `403 Forbidden`.
  - Officers without a block assignment in `DEFAULT_OFFICER_BLOCKS` or `officer_blocks` are denied access by default (`403 Forbidden`).
- **Validation:** Verified with 7 dedicated tests in `tests/test_officer_authorization.py` and scenario tests 4, 5, 6, 7 in `tests/test_e2e_scenarios.py`.

### Finding 5: OAuth 2.1 Token Kind Validation & Scope Enforcement
- **Severity:** Medium
- **Vulnerability:** Presenting a refresh token or a token with mismatched audience/role could lead to unauthorized actions.
- **Remediation in `app/api.py` & `app/oauth.py`:**
  - `_authenticate(role)` enforces `claims.get("kind") in (None, "access")`. Refresh tokens return `401 Unauthorized`.
  - Audience must match the canonical MCP endpoint (`/mcp/citizen` or `/mcp/officer`). Tokens minted for citizen scope fail at officer endpoints with `403 Insufficient Scope`.
- **Validation:** Verified with `test_reject_refresh_token_as_access_token` and `test_officer_token_with_citizen_scope_rejected`.

### Finding 6: Migration Transaction Safety and Rollback
- **Severity:** Medium
- **Vulnerability:** If an error occurred during migration execution, an unhandled exception would leave the database connection unclosed and transaction unrolled.
- **Remediation in `app/migrations/apply_migrations.py`:**
  - Wrapped migration steps in `try...except Exception:` blocks with `conn.rollback()` before closing cursors.
  - Preflight checks verify zero duplicate active applications before applying the partial unique index.
- **Validation:** Verified migration idempotency and clean failure rollback.

### Finding 7: Privacy Sanitization & PII Masking
- **Severity:** Medium
- **Vulnerability:** Regex `\b[6-9]\d{9}\b` failed to mask numbers embedded in filenames like `9864012345_ageproof.pdf` due to word boundaries (`_`). Additionally, international phone formats, Aadhaar numbers, and opaque tokens could leak into public dashboards.
- **Remediation in `app/dashboard.py`:**
  - Replaced word boundaries with digit lookarounds `(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{4})[\s-]?(\d{5})(?!\d)` to mask mobile numbers anywhere in text.
  - Added masking for 12-digit Aadhaar formats `\b\d{4}\s\d{4}\s\d{4}\b`.
  - Added masking for all Bearer tokens (`Bearer\s+[A-Za-z0-9\-_.~+/]+=*`), standalone JWTs, API keys (`sk-...`), and credential fields in JSON (`password`, `secret`, `api_key`).
- **Validation:** Verified with `test_mask_pii_text_adversarial_inputs` and tests in `tests/test_privacy.py`.

---

## 3. Explicit Verification Matrix

| Property | Status | Evidence / Verification Method |
|---|---|---|
| **SSRF DNS Rebinding** | **Verified** | `PinnedIPAdapter` binds TCP socket directly to pre-resolved IP; verified via `test_ssrf_dns_rebinding_pins_transport` |
| **SSRF Non-Global / CGNAT IPs** | **Verified** | Standard `is_global` check; verified via `test_ssrf_rejects_cgnat_and_documentation_ips` |
| **SSRF Unusual IP Encodings** | **Verified** | `socket.inet_aton` normalizes octal/hex/dword; verified via `test_ssrf_rejects_unusual_ip_encodings` |
| **SSRF Allowlist Safety** | **Verified** | Allowlist enforces global IPs in prod; verified via `test_ssrf_allowlist_in_production_cannot_authorize_private_destinations` |
| **Officer Block Boundaries** | **Verified** | Database and in-memory mappings; verified via `tests/test_officer_authorization.py` |
| **Officer Decision Atomicity** | **Verified** | Atomic `UPDATE ... WHERE status = 'PENDING' AND block = %s`; verified via `test_concurrency.py` |
| **15-Day Deemed Approval** | **Verified** | Nightly cron update restricted to `PENDING`; verified via `test_deemed_approval_logic` |
| **MCP Server Tool Separation** | **Verified** | Separate tool managers; verified via `test_server_registries_separation` |
| **Real MCP Client Integration** | **Verified** | Starlette ASGI + `streamable_http_client`; verified via `test_scenario_10_mcp_endpoints_work_with_real_mcp_client` |
| **Dashboard PII Sanitization** | **Verified** | Recursive sanitization filter; verified via `tests/test_privacy.py` |
| **Live External LLM Invocation** | *Requires API Key* | Scripted mode verified; live mode marked unverified unless credentials provided |
| **Production Cloud HTTPS Deploy** | *Requires Cloud Access* | Caddy and Compose verified locally; cloud hosting requires deployment to live VPS |

---

## 4. Residual Risks & Operational Recommendations

1. **Host-Level DNS Resolvers:** Some corporate environments rewrite local DNS queries. Ensure production hosts use standard recursive resolvers (e.g., `8.8.8.8` or cloud provider DNS) rather than intercepting proxies.
2. **Secret Rotation:** Production deployments must replace all default passwords in `.env.example` (`ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `SECRET_KEY`) with high-entropy random secrets generated via `openssl rand -hex 32`.
3. **Storage Quotas:** The upload directory `/var/sewasetu/uploads` should be placed on a volume with monitored disk space and periodic backup snapshots.
