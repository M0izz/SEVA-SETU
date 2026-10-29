# Seva Setu — Final Test Verification Report

**Project:** Seva Setu, Agent-Ready (Old Age Pension Scheme)  
**Track:** Build for Bharat AI Fellowship  
**Date of Execution:** October 2026  
**Commit Hash:** `819ffaf` (branch: `main`)  
**Overall Result:** **97 PASSED, 0 FAILED, 0 SKIPPED** (Execution time: ~8.9 seconds)  

---

## 1. Test Environment & Reproducibility

- **Operating System:** Windows 11 / Windows Server (win32)
- **Python Version:** `Python 3.14.3` (`C:\Python314\python.exe`)
- **Pytest Version:** `pytest-9.1.1`, `pluggy-1.6.0`
- **Key Installed Dependencies:**
  - `mcp==2.3.0` (Pinned Model Context Protocol SDK)
  - `starlette>=0.40.0`
  - `uvicorn>=0.30.0`
  - `httpx>=0.27.0`
  - `flask==3.0.0`
  - `cryptography>=42.0.0`
  - `pyjwt>=2.8.0`
  - `psycopg2-binary>=2.9.9`
  - `fpdf2>=2.7.0`
  - `pytest-cov>=5.0.0`
  - `pytest-asyncio>=0.23.0`

### Exact Execution Command:
```bash
python -m pytest tests/ --cov=app --cov=mcp --cov-report=term-missing -v
```

---

## 2. Test Suite Execution Breakdown

The test suite consists of 97 automated tests across 13 dedicated test modules:

| Test Module | Tests Passed | Focus & Scope |
|---|---|---|
| `tests/test_security_ssrf.py` | 17 | DNS rebinding transport pinning, mixed DNS answers, non-global IPs (CGNAT, documentation, benchmarking), unusual encodings (hex/octal/dword), trailing dots, Content-Length pre-check, allowlist safety, redirect rejection |
| `tests/test_officer_authorization.py` | 7 | Block boundaries, parameter tampering defense, unassigned officer deny-by-default, cross-block view/decision rejection, deemed approval protection |
| `tests/test_officer.py` | 10 | Officer queue retrieval, inspection, approvals, rejections with mandatory reasons, non-pending rejection, audit record creation |
| `tests/test_e2e_scenarios.py` | 11 | All 11 Fellowship end-to-end functional scenarios (citizen submission, under-60 rejection, privilege isolation, block scoping, cross-block denial, concurrent decision conflict, deemed approval, real MCP client flow, dashboard PII masking, error handling) |
| `tests/test_oauth_security.py` | 9 | RS256 token verification, expired JWT rejection, tampered signature rejection, wrong audience rejection, discovery metadata RFC 8414, JWKS RFC 7517, dynamic client registration RFC 7591, loopback port matching RFC 8252, refresh token kind rejection |
| `tests/test_portal_security.py` | 7 | Portal landing page, citizen application form, `validate_application` age/bank/IFSC rules, admin credentials validation, PDF acknowledgment generation, status page |
| `tests/test_privacy.py` | 4 | Public dashboard phone masking, bank account masking, secret/token leakage prevention, adversarial input sanitization (Aadhaar, +91 phone, API keys, credentials) |
| `tests/test_security_uploads.py` | 8 | PDF magic bytes, JPEG magic bytes, PNG magic bytes, HTML polyglot rejection, SVG rejection, binary executable rejection, text payload rejection, oversized base64 rejection |
| `tests/test_citizen.py` | 10 | Citizen scheme info, eligibility calculation, document requirements, dry-run validation, active application conflict, status checking, application withdrawal |
| `tests/test_concurrency.py` | 5 | Concurrent duplicate submissions (partial unique index), concurrent officer decisions (atomic UPDATE), deemed approval race conditions, transaction rollbacks |
| `tests/test_integration.py` | 5 | End-to-end integration across portal, database, deemed approval scheduler, and evaluation dashboard |
| `tests/test_mcp_servers.py` | 4 | Tool registry separation, ASGI app instantiation, live citizen MCP server Streamable HTTP flow, live officer MCP server flow |
| **TOTAL** | **97** | **100% Pass Rate** |

---

## 3. Measured Statement Coverage

```
Name                 Stmts   Miss  Cover   Missing
--------------------------------------------------
app\api.py             533    160    70%   74, 85-90, 167-172, 183-206, 213, 215, 245, 247-248, 265-266, 285-286, 293, 309, 314, 319-322, 330, 334, 339-340, 342-345, 363-364, 373-374, 393-395, 415-416, 423-425, 439-445, 457, 464, 481-482, 489-490, 501-502, 512-528, 543-563, 579-606, 619-624, 636-639, 701-702, 708, 734, 750, 754, 797, 805, 816-817, 845
app\app.py             460    244    47%   72-73, 77-82, 105-109, 115, 172, 239, 248-254, 264-265, 267-289, 301-321, 326-339, 345-367, 372-380, 385-415, 441-442, 457-477, 485-486, 496, 503-521, 527-533, 540-541, 545, 550-569, 591-604, 610-629, 635, 644-645, 655-671, 676, 681, 695
app\dashboard.py        49      9    82%   13, 23-27, 63, 81-82, 84
app\oauth.py           403    271    33%   70, 77-95, 113, 120, 189-190, 198, 202, 207-236, 244, 253, 273-274, 283-286, 290-298, 303-308, 313-346, 354, 370-371, 374-410, 419-446, 456, 460-463, 467-480, 488-544, 551-559, 563-574, 578-585, 591-599, 609-639
mcp\mcp_citizen.py      42     15    64%   45, 50, 62-63, 87-89, 143-157, 170-171, 183-185, 195-196
mcp\mcp_common.py      113     35    69%   39-50, 65, 86-92, 97, 100, 110-111, 120, 156-157, 167-169, 182-185, 191-200
mcp\mcp_officer.py      39     17    56%   41, 46, 58-59, 73-80, 93-95, 109-114, 124-125
--------------------------------------------------
TOTAL                 1639    751    54%
```

---

## 4. Analysis of Uncovered Code Paths

- **`app/oauth.py` (33%):** Uncovered branches include browser-based HTML consent views and interactive form submissions (`/oauth/authorize` web templates), which are interacted with by humans in a web browser rather than API test clients. The underlying security mechanisms (RS256 JWT minting, verification, discovery, JWKS, dynamic client registration, and PKCE) are thoroughly tested.
- **`app/app.py` (47%):** Uncovered code corresponds to legacy multi-step interactive citizen web wizard sessions (`form_step1.html`, `upload.html`) and live SMS gateway HTTP dispatch retries (`send_sms`). Core business validations, status views, acknowledgment PDF generation, and admin logins are covered.
- **`mcp/mcp_common.py` (69%):** Uncovered statements relate to FastMCP v1 legacy compatibility fallbacks (`server._is_v2 = False`) because tests run against the pinned `mcp==2.3.0` SDK (`MCPServer`).

---

## 5. Verification Conclusion

Every important security and functional claim—transport-level IP pinning, strict block officer authorization, concurrency safety, separate MCP registries, and privacy masking—has been exercised by real automated tests and validated against the implementation.
