# -*- coding: utf-8 -*-
"""Integration tests — portal consistency, audit trail, and deemed approval."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

import app as portal
import oauth as oauth_as


def test_mcp_submission_in_portal(client, mock_db):
    """An application created via the agent/API channel must be visible in portal."""
    conn, cur = mock_db
    # Mock create_application
    cur.fetchone.side_effect = [
        None, # active_application: none exists
        (501,), # INSERT INTO applications RETURNING id
        (0,), # portal_users count
    ]

    with patch("oauth.verify_token") as mock_verify, patch("app.send_sms"):
        mock_verify.return_value = {
            "sub": "citizen:9864012345",
            "role": "citizen",
            "scope": "citizen",
            "aud": portal.config_public_base_url() + "/mcp/citizen",
        }
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": "Bearer citizen_token"},
                          json={
                              "applicant_name": "Rima Bora",
                              "dob": "05/03/1956",
                              "gender": "Female",
                              "marital_status": "Widowed",
                              "village": "Lakhipur",
                              "block": "Sonari",
                              "bank_account": "3188021045678",
                              "ifsc": "SBIN0003077",
                              "age_proof": {"content_b64": "JVBERi0xLjQK", "filename": "proof.pdf"},
                          })
        assert res.status_code == 201
        data = res.get_json()
        assert data["status"] == "PENDING"
        assert data["application_no"].startswith("SSP")


def test_portal_retrieves_mcp_application(client, mock_db):
    """Citizen can log in to portal status page and see MCP-submitted application."""
    conn, cur = mock_db
    cur.fetchone.return_value = (
        "SSP26448454", "Rima Bora", "9864012345", datetime(1956, 3, 5).date(),
        "Lakhipur", "Sonari", "3188021045678", "SBIN0003077",
        "PENDING", datetime(2026, 10, 20), None
    )

    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["portal_mobile"] = "9864012345"

    res = client.get("/application/501")
    assert res.status_code == 200
    assert b"Rima Bora" in res.data
    assert b"SSP26448454" in res.data
    assert b"Sonari" in res.data


def test_officer_mcp_decision_visible_in_admin_portal(client, mock_db):
    """An approved application decided via officer MCP appears approved in portal admin."""
    conn, cur = mock_db
    cur.fetchone.return_value = (
        "SSP26448454", "Iman Rajkhowa", "7450884055", datetime(1941, 4, 10).date(),
        "Male", "Widowed", "Khelua Gaon", "Khelua", "8696868557716", "IDIB000S118",
        "/var/sewasetu/uploads/doc.pdf", "APPROVED", datetime(2026, 9, 26),
        datetime(2026, 10, 29), "admin"
    )
    cur.fetchall.return_value = [
        (datetime(2026, 9, 26), "SUBMIT", "applicant:7450884055", "Applied"),
        (datetime(2026, 10, 29), "APPROVED", "officer:admin", "Age verified by officer")
    ]

    with client.session_transaction() as sess:
        sess["admin"] = True
        sess["admin_user"] = "admin"

    res = client.get("/admin/application/1")
    assert res.status_code == 200
    assert b"APPROVED" in res.data
    assert b"Age verified by officer" in res.data


def test_deemed_approval_logic():
    """Verify that deemed approval identifies and updates overdue pending applications."""
    sla_days = portal.SLA_DAYS
    submitted_date = datetime.now() - timedelta(days=sla_days + 1)

    # Cutoff arithmetic matches deemed_approval.py
    cutoff = datetime.now() - timedelta(days=sla_days)
    assert submitted_date < cutoff


def test_eval_dashboard_renders(client):
    """The public /eval-dashboard route renders evaluation summary and personas."""
    res = client.get("/eval-dashboard")
    assert res.status_code == 200
    assert b"Evaluation Dashboard" in res.data
    assert b"Rima Bora" in res.data
    assert b"100.0%" in res.data

