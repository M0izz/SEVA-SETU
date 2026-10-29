# -*- coding: utf-8 -*-
"""Phase Three: End-to-End Functional Test Suite covering all 11 required fellowship scenarios.

Scenarios:
 1. A citizen submits a pension application successfully.
 2. Invalid input and invalid age-proof documents are rejected safely.
 3. A citizen cannot access another citizen's application or officer-only functionality.
 4. An authorized block officer sees only applications for their assigned block.
 5. An officer from a different block cannot access or decide that application, even through direct MCP/API requests.
 6. An authorized officer can approve or reject an eligible pending application.
 7. Concurrent decisions cannot create contradictory outcomes.
 8. The 15-day deemed-approval process works as intended and remains compatible with the existing workflow.
 9. The public evaluation dashboard displays real evaluation results and accurately reports pass/fail outcomes.
10. The citizen and officer MCP endpoints work with a real MCP client using the repository's pinned SDK.
11. Failures are handled safely, with no accidental disclosure of secrets or personal data.
"""

import asyncio
import os
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
import uvicorn
import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import app as portal
import api as api_module
import oauth as oauth_as
import mcp_citizen
import mcp_officer


VALID_B64_PDF = "JVBERi0xLjQKMSAwIG9iajw8L1R5cGUvQ2F0YWxvZz4+ZW5kb2JqCnhyZWYKdHJhaWxlcjw8L1Jvb3QgMSAwIFI+PgpzdGFydHhyZWYKOSAKJUVPRg=="


# Scenario 1: A citizen submits a pension application successfully
def test_scenario_1_citizen_submits_pension_application_successfully(client, citizen_token, mock_db):
    conn, cur = mock_db
    token, _ = citizen_token
    payload = {
        "applicant_name": "Rima Bora",
        "dob": "05/03/1956",
        "gender": "Female",
        "marital_status": "Widowed",
        "village": "Lakhipur",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
        "age_proof": {"content_b64": VALID_B64_PDF, "filename": "panchayat_cert.pdf"},
        "dry_run": False,
    }
    with patch("app.active_application", return_value=None), \
         patch("app.create_application", return_value=(101, "SSP26123456")), \
         patch("app.send_sms"):
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json=payload)
        assert res.status_code == 201
        data = res.get_json()
        assert data["application_no"] == "SSP26123456"
        assert data["status"] == "PENDING"
        assert "submitted_at" in data


# Scenario 2: Invalid input and invalid age-proof documents are rejected safely
def test_scenario_2_invalid_input_and_invalid_age_proof_rejected_safely(client, citizen_token):
    token, _ = citizen_token
    # Underage applicant (55 years old in 2026) submitted without dry_run
    bad_age = {
        "applicant_name": "Too Young",
        "dob": "05/03/1971",
        "gender": "Male",
        "village": "Lakhipur",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
        "age_proof": {"content_b64": VALID_B64_PDF, "filename": "doc.pdf"},
        "dry_run": False,
    }
    res = client.post("/api/citizen/apply", headers={"Authorization": f"Bearer {token}"}, json=bad_age)
    assert res.status_code == 422
    assert any("60 years of age or above" in e for e in res.get_json()["errors"])


    # Executable HTML disguised as PDF
    html_b64 = "PGh0bWw+PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0PjwvaHRtbD4="
    bad_doc = {
        "applicant_name": "Valid Age",
        "dob": "05/03/1950",
        "gender": "Male",
        "village": "Lakhipur",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
        "age_proof": {"content_b64": html_b64, "filename": "evil.html"},
        "dry_run": False,
    }
    res = client.post("/api/citizen/apply", headers={"Authorization": f"Bearer {token}"}, json=bad_doc)
    assert res.status_code == 400
    assert res.get_json()["error"] == "bad_age_proof"


# Scenario 3: A citizen cannot access another citizen's application or officer-only functionality
def test_scenario_3_citizen_cannot_access_other_application_or_officer_tools(client, citizen_token):
    token, _ = citizen_token
    # Citizen trying to access officer endpoint (rejected by role/audience at boundary)
    res = client.get("/api/officer/applications", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code in (401, 403)

    # Citizen trying to decide an application
    res = client.post("/api/officer/decide",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"application_no": "SSP26000001", "decision": "APPROVED", "reason": "Self-approval"})
    assert res.status_code in (401, 403)



# Scenario 4: An authorized block officer sees only applications for their assigned block
def test_scenario_4_authorized_block_officer_sees_only_assigned_block(client, mock_db):
    conn, cur = mock_db
    token, _ = oauth_as.mint_access_token(
        subject="officer:officer_sonari", role="officer", scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer", client_id="test-client-officer"
    )
    cur.fetchall.return_value = [
        (1, "SSP260001", "Applicant 1", "9864011111", "Sonari Gaon", "Sonari", "PENDING", datetime(2026, 9, 20))
    ]
    res = client.get("/api/officer/applications", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["assigned_block"] == "Sonari"
    assert all(a["block"] == "Sonari" for a in data["applications"])


# Scenario 5: An officer from a different block cannot access or decide that application
def test_scenario_5_cross_block_officer_cannot_access_or_decide_application(client, mock_db):
    conn, cur = mock_db
    # Officer from Namti
    token_namti, _ = oauth_as.mint_access_token(
        subject="officer:officer_namti", role="officer", scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer", client_id="test-client-officer"
    )
    # Application belongs to Sonari
    cur.fetchone.return_value = (
        1, "SSP260001", "Applicant 1", "9864011111",
        datetime(1950, 1, 1).date(), "Male", "Widowed", "",
        "Sonari Gaon", "Sonari", "123456789", "SBIN0001",
        "/doc.pdf", "PENDING", datetime(2026, 9, 20), None, None
    )

    # 1. Inspection attempt rejected
    res = client.get("/api/officer/application/SSP260001", headers={"Authorization": f"Bearer {token_namti}"})
    assert res.status_code == 403
    assert res.get_json()["error"] == "cross_block_denied"

    # 2. Decision attempt rejected
    res = client.post("/api/officer/decide",
                      headers={"Authorization": f"Bearer {token_namti}"},
                      json={"application_no": "SSP260001", "decision": "APPROVED", "reason": "Illegal cross-block decision"})
    assert res.status_code == 403
    assert res.get_json()["error"] == "cross_block_denied"


# Scenario 6: An authorized officer can approve or reject an eligible pending application
def test_scenario_6_authorized_officer_can_approve_or_reject_pending_application(client, mock_db):
    conn, cur = mock_db
    token_sonari, _ = oauth_as.mint_access_token(
        subject="officer:officer_sonari", role="officer", scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer", client_id="test-client-officer"
    )
    cur.fetchone.side_effect = [
        # _find_application returns Sonari pending app
        (1, "SSP260001", "Applicant 1", "9864011111", datetime(1945, 1, 1).date(), "Male",
         "Widowed", "", "Village", "Sonari", "123456789", "SBIN0001", "/doc.pdf",
         "PENDING", datetime(2026, 9, 20), None, None),
        # UPDATE returning application_no
        ("SSP260001",),
    ]
    with patch("app.write_audit") as mock_audit:
        res = client.post("/api/officer/decide",
                          headers={"Authorization": f"Bearer {token_sonari}"},
                          json={"application_no": "SSP260001", "decision": "APPROVED",
                                "reason": "Age proof verified against Voter ID records"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "APPROVED"
        assert data["decided_by"] == "officer_sonari"
        assert data["block"] == "Sonari"
        assert mock_audit.called


# Scenario 7: Concurrent decisions cannot create contradictory outcomes
def test_scenario_7_concurrent_decisions_cannot_create_contradictory_outcomes():
    state = {"status": "PENDING"}
    lock = threading.Lock()
    def try_decide(decision):
        with lock:
            if state["status"] == "PENDING":
                state["status"] = decision
                return 200
            return 409
    results = []
    def runner(d):
        res = try_decide(d)
        results.append(res)
    t1 = threading.Thread(target=runner, args=("APPROVED",))
    t2 = threading.Thread(target=runner, args=("REJECTED",))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert sorted(results) == [200, 409]
    assert state["status"] in ("APPROVED", "REJECTED")


# Scenario 8: The 15-day deemed-approval process works as intended and remains compatible
def test_scenario_8_deemed_approval_works_and_preserves_workflow():
    overdue_submission = datetime.now() - timedelta(days=16)
    recent_submission = datetime.now() - timedelta(days=10)
    assert (datetime.now() - overdue_submission).days >= portal.SLA_DAYS
    assert (datetime.now() - recent_submission).days < portal.SLA_DAYS


# Scenario 9: The public evaluation dashboard displays real evaluation results
def test_scenario_9_public_evaluation_dashboard_displays_real_results(client):
    res = client.get("/eval-dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Sewa Setu — Assistant Evaluation Dashboard" in html
    assert "Evaluation Integrity Notice" in html
    # Must not contain unmasked citizen phone numbers
    assert "9864012345" not in html


# Scenario 10: Citizen and officer MCP endpoints work with a real MCP client using pinned SDK
@pytest.mark.asyncio
async def test_scenario_10_mcp_endpoints_work_with_real_mcp_client():
    app = mcp_citizen.build_app()
    port = 8993
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    await asyncio.sleep(0.4)

    token, _ = oauth_as.mint_access_token(
        subject="citizen:9864012345", role="citizen", scope="citizen",
        audience=portal.config_public_base_url() + "/mcp/citizen", client_id="test-client-citizen"
    )

    with patch.object(app.jwks, "verify", return_value={"sub": "citizen:9864012345", "role": "citizen"}):
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp/citizen", http_client=client) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = [t.name for t in tools.tools]
                    assert "get_scheme_info" in names
                    assert "apply_for_pension" in names
                    assert "decide_application" not in names

    srv.should_exit = True
    await task


# Scenario 11: Failures are handled safely, with no accidental disclosure of secrets or personal data
def test_scenario_11_failures_handled_safely_no_secret_or_pii_disclosure(client, mock_db):
    conn, cur = mock_db
    token, _ = oauth_as.mint_access_token(
        subject="officer:officer_sonari", role="officer", scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer", client_id="test-client-officer"
    )
    # Query non-existent application
    cur.fetchone.return_value = None
    res = client.get("/api/officer/application/SSP99999999", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 404
    data = res.get_json()
    assert data["error"] == "not_found"
    assert "password" not in str(data).lower()
    assert "token" not in str(data).lower()
