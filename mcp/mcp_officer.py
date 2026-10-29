# -*- coding: utf-8 -*-
"""Sewa Setu Officer MCP server (streamable HTTP, OAuth 2.1 resource server).

Lets an authenticated block officer's AI assistant inspect their queue,
review applications, inspect application history and audit trails, and record
an APPROVED/REJECTED decision — with a mandatory reason written to the audit trail
against the officer's own authenticated identity.

Endpoint: /mcp/officer
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

import mcp_common as mc

ROLE = "officer"

mcp = FastMCP(
    "sewasetu-officer",
    instructions=(
        "You assist a block officer of the Sewa Setu Old Age Pension portal. "
        "You see the departmental queue, inspect applications and application history, "
        "and record decisions — every decision REQUIRES a real, case-specific reason "
        "that will be written into the audit trail against the officer's authenticated name, "
        "so never submit vague reasons like 'ok' or 'approved'. Never decide a case "
        "the officer has not reviewed. You cannot create applications; you can only "
        "decide pending ones and inspect the queue. If a tool returns http_status 403/401, "
        "the token is invalid or expired — prompt the officer to sign in again."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp/" + ROLE,
)


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def _prd_root(request):
    return JSONResponse(mc.protected_resource_doc(ROLE))


@mcp.custom_route("/.well-known/oauth-protected-resource/mcp/" + ROLE, methods=["GET"])
async def _prd_role(request):
    return JSONResponse(mc.protected_resource_doc(ROLE))


@mcp.tool(
    title="Queue summary",
    description=(
        "Counts for the officer's dashboard: applications by status, how many "
        "PENDING ones are overdue (past the 15-day statutory SLA), and pending "
        "counts per block. Use this when the officer asks what is waiting in the queue."
    ),
)
async def queue_summary() -> dict:
    status, body = await mc.call_portal("GET", "/api/officer/summary")
    return {"http_status": status, **body}


@mcp.tool(
    title="List applications",
    description=(
        "List applications in the queue (50 per page, oldest first). Filters: status "
        "(PENDING default; APPROVED, DEEMED_APPROVED, REJECTED, WITHDRAWN, ALL), "
        "block name, or citizen mobile number. Returns application numbers, names, "
        "villages, blocks, and submission dates."
    ),
)
async def list_applications(status: str = "PENDING", block: str = "",
                            mobile: str = "", page: int = 1) -> dict:
    params = {"status": status, "page": page}
    if block:
        params["block"] = block
    if mobile:
        params["mobile"] = mobile
    status_code, body = await mc.call_portal("GET", "/api/officer/applications",
                                             params=params)
    return {"http_status": status_code, **body}


@mcp.tool(
    title="Review one application",
    description=(
        "Full detail of one application by its application number (like SSP26448454) "
        "or ID: applicant name, age today, date of birth, village, block, bank details, "
        "age-proof document reference, status, and full audit trail history. "
        "The officer must inspect this before deciding."
    ),
)
async def get_application(application_no: str) -> dict:
    status_code, body = await mc.call_portal(
        "GET", "/api/officer/application/" + str(application_no).strip())
    return {"http_status": status_code, **body}


@mcp.tool(
    title="Record an approve/reject decision",
    description=(
        "Record the officer's decision on a PENDING application: decision must be "
        "APPROVED or REJECTED and reason must be a concrete, case-specific explanation "
        "(e.g. 'Age verified against voter ID; bank details confirmed' or 'Age below 60'). "
        "The reason is stored in the immutable audit trail against the officer's name. "
        "Only PENDING applications can be decided."
    ),
)
async def decide_application(application_no: str, decision: str, reason: str) -> dict:
    status_code, body = await mc.call_portal("POST", "/api/officer/decide", json_body={
        "application_no": str(application_no).strip(),
        "decision": decision.strip().upper(),
        "reason": reason.strip(),
    })
    return {"http_status": status_code, **body}


def build_app():
    """Starlette app: OAuth resource-server guard in front of the MCP endpoint."""
    return mc.RequireBearerMiddleware(mcp.streamable_http_app(), ROLE)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0",
                port=int(os.environ.get("MCP_PORT", "8002")), log_level="warning")
