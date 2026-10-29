# Design Note — Sewa Setu: Agent-Ready

**Author:** Build for Bharat AI Fellow  
**System:** Sewa Setu Old Age Pension Portal, Department of Social Welfare, Government of Purvanchal  
**Architecture:** Model Context Protocol (MCP) over Streamable HTTP & OAuth 2.1 RFC 9728 Resource Servers  

---

## 1. The Three Decisions That Mattered Most

### Decision 1: MCP Servers as Standard OAuth 2.1 Resource Servers
Rather than inventing an ad-hoc AI authentication mechanism or trusting caller-supplied IDs in tool parameters, both the Citizen MCP (`/mcp/citizen`) and Officer MCP (`/mcp/officer`) servers are built as formal OAuth 2.1 Resource Servers (RFC 9728) backed by the portal's existing Authorization Server.
- **Interoperability & Standards:** Standard OAuth 2.1 authorization code flow with PKCE S256 is enforced.
- **Audience Isolation:** Tokens are minted with explicit RFC 8707 resource indicators (`$PUBLIC_BASE_URL/mcp/citizen` vs `$PUBLIC_BASE_URL/mcp/officer`). A citizen token presented to the officer MCP is cryptographically rejected at the ASGI middleware layer before touching any tool logic.
- **Privacy & Security on Shared Devices:** Passwords and OTPs are never exposed to the AI model or client. Citizens authenticate via mobile OTP on the portal's own trusted UI; officers authenticate via departmental login. Sessions have strict absolute lifetimes (2h for citizens, 8h for officers).

### Decision 2: Zero Duplicated Business Logic — Authoritative Portal Core
The MCP servers contain zero business logic or validation rules. Every write and check from an assistant passes from MCP to the portal JSON API (`app/api.py`), which delegates directly to the portal's canonical functions: `validate_application()`, `active_application()`, `create_application()`, and `write_audit()`.
- **System of Record Integrity:** No channel can admit an application that another channel would refuse. An applicant under 60 is rejected identically across web form, counter, and AI assistant.
- **Pilot Block Enforcement:** As mandated by the Director, the agent channel is constrained to designated pilot blocks (`Sonari` and `Namti`), while redirecting applicants from non-pilot blocks to physical block offices or the primary web portal.
- **Transactional Consistency:** Database operations utilize strict PostgreSQL transactions to guarantee duplicate prevention and audit immutability.

### Decision 3: Tool Ergonomics Optimized for Small Models and Turn Limits
Rather than exposing dozens of fine-grained API micro-endpoints that confuse LLMs and exhaust token budgets, each MCP server exposes a focused registry of four chunky, self-describing tools:
- **Citizen Tools:** `get_scheme_info`, `check_eligibility`, `get_required_documents`, `apply_for_pension`, `get_my_application`, `withdraw_my_application`.
- **Dry-Run Validation:** `apply_for_pension` includes a `dry_run=true` flag allowing the assistant to perform non-destructive pre-validation, catch errors (e.g. malformed IFSC or underage DOB) cheaply, and guide the citizen to correct them before final submission.
- **Accountable Officer Decisions:** Officer decisions strictly require a substantiated, case-specific `reason`. The decision and reason are permanently logged in `audit_log` alongside the authenticated officer's username extracted from the cryptographic token.

---

## 2. The Decision I Would Make Differently With More Time

### Read-Only Anomaly Screening for Block Officers
If granted additional time, I would expand the officer assistant beyond decision recording into an **anomaly screening and prioritization engine**.

While the current implementation allows officers to inspect queues and record decisions with mandatory justifications, block officers face a massive backlog (~700 pending applications, with hundreds exceeding the 15-day SLA). A high-value addition would be a read-only screening tool operating over the 100,000+ historical rows to surface:
1. Shared bank account numbers across unrelated applicants.
2. Sudden application clusters originating from a single village or IP address.
3. Inconsistencies between claimed date of birth and electoral records.

This was intentionally omitted from the current build because a poorly calibrated screening tool risks introducing algorithmic bias or false positives against legitimate senior citizens. Implementing accountable, auditable heuristic scoring requires thorough statistical evaluation against ground-truth field audits.
