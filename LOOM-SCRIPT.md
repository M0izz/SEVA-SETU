# Loom Video Script — 3 Minutes (Face + Screen)
# Seva Setu: Agent-Ready (Build for Bharat AI Fellowship)

**Target Duration:** 3:00  
**Format:** Picture-in-picture (face on camera + terminal / browser screen)  
**Tone:** Confident, concise, engineering-focused  

---

### [0:00 – 0:25] The Problem & Mandate (Camera on You)
- "Hello! I'm presenting the transformation of **Sewa Setu** — the Old Age Pension portal of Purvanchal — into an agent-ready public service for the Build for Bharat AI Fellowship.
- The Director gave us a strict mandate: enable ANY AI assistant to apply for pensions and let officers process them responsibly, while keeping the existing portal as the single authoritative system of record.
- We built two separate MCP servers over Streamable HTTP, backed by real OAuth 2.1 authorization, transport-level SSRF defenses, strict block-level officer boundaries, and a 100% verified test suite."

---

### [0:25 – 1:05] Citizen MCP Demonstration & Transport Defenses (Terminal + Client)
*(Screen: Terminal showing real MCP client connecting to `/mcp/citizen`)*
- "Here is our Citizen MCP server in action. We connect via a real MCP client using Streamable HTTP.
- Our persona is **Rima Bora**, 70, from Lakhipur in Sonari block — the citizen from the Director's note whose grandson tried applying via ChatGPT.
- Notice the citizen authenticates via the portal's OAuth 2.1 flow (mobile + OTP). The assistant never touches the OTP.
- The assistant calls `check_eligibility` (verifying age 70 >= 60 and open window), then calls `apply_for_pension` with her age proof.
- Under the hood, document fetching is protected against SSRF DNS rebinding using transport-level IP pinning, blocking private, CGNAT, and cloud metadata IPs.
- Result: Application **SSP26448454** created in PENDING status."

---

### [1:05 – 1:35] Authoritative Verification in Portal & Database (Browser Screen)
*(Screen: Switch to portal admin view at `/admin` and status portal)*
- "Looking at the portal: Rima Bora's application is instantly visible here in the official database.
- Key architectural point: **Zero duplicated business logic**. The MCP server has no rules of its own; it routes through `validate_application()` in `app.py`.
- If an underage applicant like Sunita Karmakar (58) applies, the portal's validator rejects it immediately.
- If an applicant from Borgaon applies, our pilot block enforcement politely redirects them to the block office.
- And concurrent duplicate applications are stopped dead by a PostgreSQL partial unique index."

---

### [1:35 – 2:10] Officer MCP Assistant & Block Authorization Boundaries (Terminal + Browser)
*(Screen: Terminal connected to `/mcp/officer` with officer token)*
- "On the officer side, the MCP server is completely separate. Citizen tokens cannot connect here due to audience binding.
- More importantly, **officer authorization is strictly scoped to their assigned administrative block**.
- Officer Sonari sees only Sonari applications. If an officer tries to inspect, list, or decide an application belonging to Namti block, the server denies it with HTTP 403 Forbidden.
- When Officer Sonari calls `decide_application`, a specific, substantiated reason is strictly mandatory.
- The decision updates the application atomically and writes the officer's name and justification into the immutable `audit_log`. Trying to decide an already approved case or overwriting a 15-day deemed approval is refused with HTTP 409."

---

### [2:10 – 2:45] Evaluation Dashboard & PostgreSQL Assertions (Browser Screen)
*(Screen: Switch to `/eval-dashboard`)*
- "We built a public evaluation dashboard at `/eval-dashboard`.
- Evaluators can review genuine results across **8 citizen personas and 2 officer scenarios**, plus cross-role security checks:
  1. Rima Bora (fresh eligible application)
  2. Anil Das (re-applying after rejection)
  3. Sunita Karmakar (underage rejection)
  4. Pratima Devi (non-pilot block redirection)
  5. Girish Hussain (duplicate prevention)
  6. Iman Gogoi (withdrawal and re-application)
  7. Bapu Kaivartta (catch-and-correct via dry-run)
  8. Jahanara Baruah (15-day deemed approval status check)
- Every test asserts against actual PostgreSQL database rows and audit logs.
- The dashboard features real-time PII sanitization (masking phones, Aadhaar, bank accounts, and tokens) and honestly labels evaluation modes.
- Our full test suite boasts **97 automated tests with 100% pass rate** and 54% statement coverage."

---

### [2:45 – 3:00] Conclusion (Camera on You)
- "Three engineering decisions made this succeed: OAuth resource servers with audience isolation, zero duplicated logic with the portal as system of record, and strict database-level integrity.
- The system is fully containerized, secure, and ready for deployment. Thank you!"
