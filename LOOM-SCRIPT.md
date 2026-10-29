# Loom Video Script — 3 Minutes (Face + Screen)

**Target Duration:** 3:00  
**Format:** Picture-in-picture (face on camera + terminal / browser screen)  
**Tone:** Confident, concise, engineering-focused  

---

### [0:00 – 0:25] The Problem & Mandate (Camera on You)
- "Hello! I'm presenting the transformation of Sewa Setu — the Old Age Pension portal of Purvanchal — into an agent-ready service.
- The Director gave us a clear mandate: enable ANY AI assistant to apply for pensions and let officers process them responsibly, while keeping the existing portal as the single system of record.
- We built two separate MCP servers over streamable HTTP, backed by real OAuth 2.1 authorization and evaluated against real PostgreSQL database states."

---

### [0:25 – 1:05] Live Citizen MCP Demonstration (Terminal + Client)
*(Screen: Terminal showing real MCP client connecting to `/mcp/citizen`)*
- "Here is our Citizen MCP server in action. We connect via a real MCP client using Streamable HTTP.
- Our persona is **Rima Bora**, 70, from Lakhipur in Sonari block — the exact citizen from the Director's note whose grandson tried applying via ChatGPT.
- Notice the citizen authenticates via the portal's OAuth 2.1 flow (mobile + OTP). The assistant never touches the OTP.
- The assistant calls `check_eligibility` (verifying age 70 >= 60 and open window), then calls `apply_for_pension` with her age proof.
- Result: Application **SSP26...** created in PENDING status."

---

### [1:05 – 1:35] Authoritative Verification in Portal & Database (Browser Screen)
*(Screen: Switch to portal admin view at `http://localhost:8000/admin` and status portal)*
- "Looking at the portal: Rima Bora's application is instantly visible here in the official database.
- Key architectural point: **Zero duplicated business logic**. The MCP server has no rules of its own; it routes through `validate_application()` in `app.py`.
- If an underage applicant like Sunita Karmakar (58) applies, the portal's validator rejects it immediately.
- If an applicant from Borgaon applies, our pilot block enforcement politely redirects them to the block office."

---

### [1:35 – 2:10] Officer MCP Assistant & Accountability (Terminal + Browser)
*(Screen: Terminal connected to `/mcp/officer` with officer token)*
- "On the officer side, the MCP server is completely separate. Citizen tokens cannot even establish a connection here due to audience binding.
- The block officer calls `queue_summary` and inspects pending cases.
- When the officer calls `decide_application`, a specific, substantiated reason is strictly mandatory.
- The decision records the officer's authenticated username and the justification directly in the immutable `audit_log`. Trying to decide an already approved case is refused with HTTP 409."

---

### [2:10 – 2:45] Evaluation Dashboard & PostgreSQL Assertions (Browser Screen)
*(Screen: Switch to `http://localhost:8000/eval-dashboard`)*
- "We built a public evaluation dashboard at `/eval-dashboard`.
- We tested **8 citizen personas and 2 officer scenarios**, plus cross-role security checks:
  1. Rima Bora (fresh eligible application)
  2. Anil Das (re-applying after rejection)
  3. Sunita Karmakar (underage rejection)
  4. Pratima Devi (non-pilot block redirection)
  5. Girish Hussain (duplicate prevention)
  6. Iman Gogoi (withdrawal and re-application)
  7. Bapu Kaivartta (catch-and-correct via dry-run)
  8. Jahanara Baruah (15-day deemed approval status check)
- Every single test verifies actual PostgreSQL database rows and audit logs — not just LLM text output.
- All 27 automated tests and real MCP evaluations passed with a **100% pass rate**."

---

### [2:45 – 3:00] Conclusion (Camera on You)
- "Three decisions made this succeed: OAuth resource servers with audience isolation, zero duplicated logic, and tools built for small models.
- The system is fully containerized, secure, and ready for deployment. Thank you!"
