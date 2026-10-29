# -*- coding: utf-8 -*-
"""Sewa Setu Citizen MCP server (streamable HTTP, OAuth 2.1 resource server).

Lets ANY AI assistant act for an authenticated citizen on the Old Age Pension
portal: retrieve scheme information, check eligibility, inspect required documents,
prepare/validate an application, submit an application, follow up on status, and withdraw.
Same rules as the web form, pilot blocks only, portal stays the system of record.

Endpoint: /mcp/citizen
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.responses import JSONResponse
import mcp_common as mc

ROLE = "citizen"

mcp = mc.create_mcp_server(
    "sewasetu-citizen",
    ROLE,
    instructions=(
        "You help an elderly citizen of Purvanchal with the Old Age Pension "
        "(Rs.250/month, age 60+). You are acting for ONE signed-in citizen — "
        "the tools only ever see and change that citizen's own data. "
        "Rules you must never break: (1) never invent scheme facts — amounts, "
        "ages, deadlines come only from tool results; (2) the citizen's date of "
        "birth must be DD/MM/YYYY; (3) applications through this agent channel "
        "are accepted only for the pilot blocks the tools report (Sonari, Namti); "
        "(4) a pension application needs an age-proof document — use the document link "
        "the citizen actually gives you, never a placeholder or fabricated file; "
        "(5) if the citizen already has an active application, do not try to submit another — "
        "explain and, only if asked, withdraw a PENDING one; (6) validate with "
        "dry_run=true first when details are uncertain."
    ),
)



@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def _prd_root(request):
    return JSONResponse(mc.protected_resource_doc(ROLE))


@mcp.custom_route("/.well-known/oauth-protected-resource/mcp/" + ROLE, methods=["GET"])
async def _prd_role(request):
    return JSONResponse(mc.protected_resource_doc(ROLE))


@mcp.tool(
    title="Get scheme information",
    description=(
        "Retrieve official Old Age Pension scheme rules, benefit amount (Rs. 250/month), "
        "eligibility criteria (age 60+), statutory SLA (15 days under Purvanchal Right to "
        "Public Services Act), and active pilot blocks."
    ),
)
async def get_scheme_info() -> dict:
    status, body = await mc.call_portal("GET", "/api/citizen/me")
    return {
        "http_status": status,
        "scheme_name": "Indira Gandhi National Old Age Pension Scheme (IGNOAPS)",
        "department": "Department of Social Welfare, Government of Purvanchal",
        "pension_amount_per_month_inr": 250,
        "minimum_age": 60,
        "processing_sla_days": 15,
        "statutory_deemed_approval": "Applications pending over 15 days stand deemed approved by law",
        "agent_pilot_blocks": body.get("pilot_blocks", ["Sonari", "Namti"]),
        "authenticated_citizen_mobile": body.get("mobile"),
        "active_application": body.get("active_application"),
    }


@mcp.tool(
    title="Check pension eligibility",
    description=(
        "Read-only check whether the signed-in citizen may apply for the Old Age "
        "Pension: verifies age from date_of_birth (DD/MM/YYYY), whether the scheme "
        "window is open, and whether this mobile number already holds an active "
        "application. Submits nothing. Run this before applying."
    ),
)
async def check_eligibility(date_of_birth: str) -> dict:
    status, body = await mc.call_portal("GET", "/api/citizen/eligibility",
                                        params={"dob": date_of_birth})
    return {"http_status": status, **body}


@mcp.tool(
    title="Get required application documents",
    description=(
        "Retrieve the list of required documents and acceptable age-proof documents "
        "(Panchayat certificate, Aadhaar, Voter ID card, or Matriculation certificate) "
        "and specifications (JPG, PNG, or PDF, max 5 MB)."
    ),
)
async def get_required_documents() -> dict:
    return {
        "required_documents": [
            {
                "type": "Age Proof Document",
                "mandatory": True,
                "description": "Proof establishing applicant is 60 years of age or older",
                "acceptable_documents": [
                    "Gram Panchayat Age Certificate / Gaonburah Certificate",
                    "Aadhaar Card",
                    "Electoral Photo Identity Card (EPIC / Voter ID)",
                    "School / Matriculation Certificate",
                ],
                "allowed_formats": ["pdf", "jpg", "jpeg", "png"],
                "max_size_bytes": 5 * 1024 * 1024,
                "max_size_human": "5 MB",
            },
            {
                "type": "Bank Account Details",
                "mandatory": True,
                "description": "Applicant's active savings bank account number (9 to 18 digits) and valid IFSC",
            },
        ]
    }


@mcp.tool(
    title="Apply for the old age pension",
    description=(
        "Submit the citizen's Old Age Pension application (or validate without "
        "submitting when dry_run=true). All fields are required except husband_name. "
        "date_of_birth format DD/MM/YYYY. block must be one of the agent-pilot "
        "blocks (Sonari or Namti); for other blocks politely send the citizen to "
        "the portal or block office. bank_account: 9-18 digits. ifsc: 11 characters "
        "like SBIN0003077. age_proof_url: the citizen's own age-proof document "
        "(JPG/PNG/PDF, max 5MB) as an accessible URL — never invent or reuse another "
        "person's document. On success returns the application number."
    ),
)
async def apply_for_pension(full_name: str, date_of_birth: str, gender: str,
                            marital_status: str, village: str, block: str,
                            bank_account: str, ifsc: str, age_proof_url: str,
                            husband_name: str = "", dry_run: bool = False) -> dict:
    payload = {
        "applicant_name": full_name,
        "dob": date_of_birth,
        "gender": gender,
        "marital_status": marital_status,
        "husband_name": husband_name,
        "village": village,
        "block": block,
        "bank_account": bank_account,
        "ifsc": ifsc,
        "age_proof": {"url": age_proof_url},
        "dry_run": dry_run,
    }
    status, body = await mc.call_portal("POST", "/api/citizen/apply", json_body=payload)
    return {"http_status": status, **body}


@mcp.tool(
    title="Get my application status",
    description=(
        "Look up the signed-in citizen's latest pension application: status "
        "(PENDING / APPROVED / DEEMED_APPROVED / REJECTED / WITHDRAWN), application "
        "number, what was decided and by whom, and how many SLA days are left while "
        "pending (the law approves after 15 days). Use this for status queries and history."
    ),
)
async def get_my_application() -> dict:
    status, body = await mc.call_portal("GET", "/api/citizen/application")
    return {"http_status": status, **body}


@mcp.tool(
    title="Withdraw my pending application",
    description=(
        "Withdraw the citizen's own PENDING application so they can apply afresh "
        "(e.g. to fix bank details). Refuses when there is nothing pending. Only "
        "do this when the citizen clearly requests withdrawal."
    ),
)
async def withdraw_my_application(application_no: str = "") -> dict:
    status, body = await mc.call_portal("POST", "/api/citizen/withdraw",
                                        json_body={"application_no": application_no})
    return {"http_status": status, **body}


def build_app():
    """Starlette app: OAuth resource-server guard in front of the MCP endpoint."""
    return mc.build_mcp_app(mcp, ROLE)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0",
                port=int(os.environ.get("MCP_PORT", "8001")), log_level="warning")
