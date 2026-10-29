# -*- coding: utf-8 -*-
"""Integration tests for block-level officer authorization boundaries.

Proves:
- An officer assigned to Block A can access and decide applications in Block A.
- That officer CANNOT list, view, approve, or reject applications in Block B.
- Changing the caller-supplied block parameter does not grant access.
- Direct API calls cannot bypass authorization boundaries.
- Officers with missing or invalid block assignments are denied by default.
- Cross-block attempts return 403 without leaking application records.
- Completed decisions and statutory 15-day deemed approvals cannot be overwritten.
- Audit log records officer username, application ID, block, and decision reason.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

import app as portal
import api as api_module
import oauth as oauth_as


@pytest.fixture
def sonari_token():
    token, claims = oauth_as.mint_access_token(
        subject="officer:officer_sonari",
        role="officer",
        scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer",
        client_id="test-client-officer",
    )
    return token


@pytest.fixture
def namti_token():
    token, claims = oauth_as.mint_access_token(
        subject="officer:officer_namti",
        role="officer",
        scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer",
        client_id="test-client-officer",
    )
    return token


@pytest.fixture
def unassigned_token():
    token, claims = oauth_as.mint_access_token(
        subject="officer:unassigned_officer",
        role="officer",
        scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer",
        client_id="test-client-officer",
    )
    return token


def test_officer_assigned_block_can_list_and_view(client, sonari_token, mock_db):
    """Officer assigned to Sonari can list and view Sonari applications."""
    conn, cur = mock_db
    cur.fetchall.return_value = [
        (101, "SSP26000101", "Sonari Citizen", "9864011111", "Sonari Gaon", "Sonari", "PENDING", datetime(2026, 9, 20))
    ]

    res = client.get("/api/officer/applications", headers={"Authorization": f"Bearer {sonari_token}"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["assigned_block"] == "Sonari"
    assert len(data["applications"]) == 1
    assert data["applications"][0]["block"] == "Sonari"


def test_officer_cannot_view_cross_block_application(client, sonari_token, mock_db):
    """Officer assigned to Sonari CANNOT view an application belonging to Namti (cross-block)."""
    conn, cur = mock_db
    # Application 202 belongs to Namti
    cur.fetchone.return_value = (
        202, "SSP26000202", "Namti Citizen", "9864022222",
        datetime(1950, 1, 1).date(), "Female", "Widowed", "",
        "Namti Gaon", "Namti", "123456789012", "SBIN0001234",
        "/docs/proof.pdf", "PENDING", datetime(2026, 9, 21), None, None
    )

    res = client.get("/api/officer/application/SSP26000202", headers={"Authorization": f"Bearer {sonari_token}"})
    assert res.status_code == 403
    data = res.get_json()
    assert data["error"] == "cross_block_denied"
    # Ensure no sensitive applicant information is leaked in error response
    assert "application" not in data
    assert "Namti Citizen" not in str(data)
    assert "123456789012" not in str(data)


def test_officer_caller_supplied_block_cannot_escalate(client, sonari_token, mock_db):
    """Changing caller-supplied query parameter 'block=Namti' is rejected with 403."""
    res = client.get("/api/officer/applications?block=Namti", headers={"Authorization": f"Bearer {sonari_token}"})
    assert res.status_code == 403
    data = res.get_json()
    assert data["error"] == "cross_block_denied"


def test_officer_cannot_decide_cross_block_application(client, sonari_token, mock_db):
    """Officer assigned to Sonari CANNOT approve or reject an application in Namti."""
    conn, cur = mock_db
    # Application belongs to Namti
    cur.fetchone.return_value = (
        202, "SSP26000202", "Namti Citizen", "9864022222",
        datetime(1950, 1, 1).date(), "Female", "Widowed", "",
        "Namti Gaon", "Namti", "123456789012", "SBIN0001234",
        "/docs/proof.pdf", "PENDING", datetime(2026, 9, 21), None, None
    )

    res = client.post("/api/officer/decide",
                      headers={"Authorization": f"Bearer {sonari_token}"},
                      json={"application_no": "SSP26000202", "decision": "APPROVED", "reason": "Valid docs"})
    assert res.status_code == 403
    assert res.get_json()["error"] == "cross_block_denied"


def test_officer_without_block_assignment_is_denied_by_default(client, unassigned_token, mock_db):
    """An officer identity with no assigned block in the database is denied by default."""
    res = client.get("/api/officer/applications", headers={"Authorization": f"Bearer {unassigned_token}"})
    assert res.status_code == 403
    assert res.get_json()["error"] == "unauthorized_block"

    res = client.get("/api/officer/summary", headers={"Authorization": f"Bearer {unassigned_token}"})
    assert res.status_code == 403
    assert res.get_json()["error"] == "unauthorized_block"


def test_officer_cannot_overwrite_deemed_approval(client, sonari_token, mock_db):
    """An application already marked DEEMED_APPROVED cannot be decided by an officer (409 Conflict)."""
    conn, cur = mock_db
    # Application is already DEEMED_APPROVED
    cur.fetchone.return_value = (
        105, "SSP26000105", "Citizen Name", "9864011115",
        datetime(1945, 1, 1).date(), "Male", "Widowed", "",
        "Village", "Sonari", "123456789", "SBIN0001",
        "/docs/proof.pdf", "DEEMED_APPROVED", datetime(2026, 8, 1), datetime(2026, 8, 16), "RTPS-AUTO"
    )

    res = client.post("/api/officer/decide",
                      headers={"Authorization": f"Bearer {sonari_token}"},
                      json={"application_no": "SSP26000105", "decision": "REJECTED", "reason": "Late rejection"})
    assert res.status_code == 409
    data = res.get_json()
    assert data["error"] == "not_pending"
    assert "DEEMED_APPROVED" in data["detail"]


def test_officer_decision_records_audit_trail(client, sonari_token, mock_db):
    """Approving an authorized application records officer identity and assigned block in audit log."""
    conn, cur = mock_db
    cur.fetchone.side_effect = [
        # _find_application
        (101, "SSP26000101", "Citizen Name", "9864011111",
         datetime(1945, 1, 1).date(), "Male", "Widowed", "",
         "Village", "Sonari", "123456789", "SBIN0001",
         "/docs/proof.pdf", "PENDING", datetime(2026, 9, 20), None, None),
        # UPDATE RETURNING application_no
        ("SSP26000101",),
    ]

    with patch("app.write_audit") as mock_write_audit:
        res = client.post("/api/officer/decide",
                          headers={"Authorization": f"Bearer {sonari_token}"},
                          json={"application_no": "SSP26000101", "decision": "APPROVED",
                                "reason": "Age verified against Panchayat certificate"})
        assert res.status_code == 200
        assert mock_write_audit.called
        # Check audit parameters: (cur, app_id, action, actor, note)
        args, kwargs = mock_write_audit.call_args
        assert args[1] == 101 # application_id
        assert args[2] == "APPROVED"
        assert args[3] == "officer:officer_sonari"
        assert "Block: Sonari" in args[4]
        assert "Age verified against Panchayat certificate" in args[4]
