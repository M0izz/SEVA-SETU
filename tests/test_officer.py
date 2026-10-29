# -*- coding: utf-8 -*-
"""Officer workflow tests — unit, schema, and security."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

import app as portal


def test_queue_retrieval(client, mock_db):
    conn, cur = mock_db
    cur.fetchall.side_effect = [
        [("PENDING", 700), ("APPROVED", 90000)],
        [("Sonari", 200), ("Namti", 150)],
    ]
    cur.fetchone.return_value = (699,)

    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        res = client.get("/api/officer/summary",
                         headers={"Authorization": "Bearer officer_tok"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["officer"] == "admin"
        assert data["by_status"]["PENDING"] == 700
        assert data["overdue_pending"] == 699


def test_application_inspection(client, mock_db):
    conn, cur = mock_db
    # Mock row returned by _find_application
    cur.fetchone.return_value = (
        1, "SSP26448454", "Iman Rajkhowa", "7450884055",
        datetime(1941, 4, 10).date(), "Male", "Widowed", "",
        "Khelua Gaon", "Khelua", "8696868557716", "IDIB000S118",
        "/var/sewasetu/uploads/doc.pdf", "PENDING",
        datetime(2026, 9, 26, 12, 34), None, None
    )
    cur.fetchall.return_value = [
        (datetime(2026, 9, 26, 12, 34), "SUBMIT", "applicant:7450884055", "Initial application")
    ]

    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        res = client.get("/api/officer/application/SSP26448454",
                         headers={"Authorization": "Bearer officer_tok"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["application"]["application_no"] == "SSP26448454"
        assert data["application"]["applicant_name"] == "Iman Rajkhowa"
        assert data["application"]["age_today"] is not None
        assert len(data["audit"]) == 1


def test_successful_approval(client, mock_db):
    conn, cur = mock_db
    cur.fetchone.side_effect = [
        (1, "SSP26448454", "Iman Rajkhowa", "7450884055", datetime(1941, 4, 10).date(), "Male",
         "Widowed", "", "Khelua Gaon", "Khelua", "8696868557716", "IDIB000S118", "/doc.pdf",
         "PENDING", datetime(2026, 9, 26), None, None), # _find_application
        ("SSP26448454",), # UPDATE returning application_no
    ]

    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        res = client.post("/api/officer/decide",
                          headers={"Authorization": "Bearer officer_tok"},
                          json={
                              "application_no": "SSP26448454",
                              "decision": "APPROVED",
                              "reason": "Age proof verified against Panchayat records"
                          })
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "APPROVED"
        assert data["decided_by"] == "admin"
        assert data["reason"] == "Age proof verified against Panchayat records"


def test_successful_rejection(client, mock_db):
    conn, cur = mock_db
    cur.fetchone.side_effect = [
        (1, "SSP26448454", "Applicant", "7450884055", datetime(1968, 4, 10).date(), "Male",
         "Widowed", "", "Village", "Sonari", "8696868557716", "IDIB000S118", "/doc.pdf",
         "PENDING", datetime(2026, 9, 26), None, None),
        ("SSP26448454",),
    ]

    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        res = client.post("/api/officer/decide",
                          headers={"Authorization": "Bearer officer_tok"},
                          json={
                              "application_no": "SSP26448454",
                              "decision": "REJECTED",
                              "reason": "Applicant age is under 60 years"
                          })
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "REJECTED"
        assert data["decided_by"] == "admin"


def test_rejection_without_reason(client, mock_db):
    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        # Missing reason
        res = client.post("/api/officer/decide",
                          headers={"Authorization": "Bearer officer_tok"},
                          json={
                              "application_no": "SSP26448454",
                              "decision": "REJECTED",
                              "reason": ""
                          })
        assert res.status_code == 422
        assert res.get_json()["error"] == "reason_required"


def test_decision_on_non_pending_application(client, mock_db):
    conn, cur = mock_db
    # Application is already APPROVED
    cur.fetchone.side_effect = [
        (1, "SSP26448454", "Applicant", "7450884055", datetime(1941, 4, 10).date(), "Male",
         "Widowed", "", "Village", "Sonari", "8696868557716", "IDIB000S118", "/doc.pdf",
         "APPROVED", datetime(2026, 9, 26), datetime(2026, 10, 1), "admin"),
        None, # UPDATE returns nothing because status != 'PENDING'
    ]

    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        res = client.post("/api/officer/decide",
                          headers={"Authorization": "Bearer officer_tok"},
                          json={
                              "application_no": "SSP26448454",
                              "decision": "APPROVED",
                              "reason": "Attempting approval again"
                          })
        assert res.status_code == 409
        assert res.get_json()["error"] == "not_pending"


def test_unauthorized_officer(client):
    # Missing token
    res = client.get("/api/officer/summary")
    assert res.status_code == 401
    assert res.get_json()["error"] == "missing_bearer_token"


def test_officer_token_with_citizen_scope_rejected(client):
    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "citizen:9864012345",
            "role": "citizen",
            "scope": "citizen",
        }
        res = client.get("/api/officer/summary",
                         headers={"Authorization": "Bearer citizen_tok"})
        assert res.status_code == 403
        assert res.get_json()["error"] == "insufficient_scope"


def test_decision_audit_record(mock_db):
    conn, cur = mock_db
    portal.write_audit(cur, 101, "APPROVED", "officer:admin", "Document verified")
    cur.execute.assert_called_with(
        "INSERT INTO audit_log (application_id, action, actor, note, at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (101, "APPROVED", "officer:admin", "Document verified", cur.execute.call_args[0][1][4])
    )


def test_correct_officer_identity(client, mock_db):
    conn, cur = mock_db
    cur.fetchone.side_effect = [
        (1, "SSP26448454", "Iman", "7450884055", datetime(1941, 4, 10).date(), "Male",
         "Widowed", "", "Village", "Sonari", "8696868557716", "IDIB000S118", "/doc.pdf",
         "PENDING", datetime(2026, 9, 26), None, None),
        ("SSP26448454",),
    ]

    # Specific officer username 'block_officer_sonari' in token
    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:block_officer_sonari",
            "role": "officer",
            "scope": "officer",
            "aud": portal.config_public_base_url() + "/mcp/officer",
        }
        res = client.post("/api/officer/decide",
                          headers={"Authorization": "Bearer officer_tok"},
                          json={
                              "application_no": "SSP26448454",
                              "decision": "APPROVED",
                              "reason": "Verified on ground"
                          })
        assert res.status_code == 200
        assert res.get_json()["decided_by"] == "block_officer_sonari"
