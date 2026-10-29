# -*- coding: utf-8 -*-
"""Verification tests for the authoritative Flask portal (app/app.py):
form validations, admin authentication, status lookup, and PDF acknowledgment generation."""

import os
import io
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
import app as portal


def test_portal_home_page(client):
    """Citizen landing page renders successfully with scheme information."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "Sewa Setu" in html or "Old Age Pension" in html


def test_portal_apply_page_get(client):
    """Citizen application form renders successfully."""
    res = client.get("/apply")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "Security Check" in html or "mobile" in html.lower()


def test_portal_validate_application_rules():
    """Unit test validate_application: age under 60, bank account format, IFSC format."""
    # 1. Underage applicant
    errors, cleaned = portal.validate_application({
        "applicant_name": "Young Applicant",
        "dob": "01/01/2000",
        "gender": "Male",
        "marital_status": "Single",
        "village": "Sonari Gaon",
        "block": "Sonari",
        "bank_account": "1234567890",
        "ifsc": "SBIN0003077",
    }, doc_path="test.pdf")
    assert any("60 years" in e for e in errors)

    # 2. Invalid IFSC code
    errors_ifsc, _ = portal.validate_application({
        "applicant_name": "Elder Citizen",
        "dob": "01/01/1950",
        "gender": "Male",
        "marital_status": "Married",
        "village": "Sonari Gaon",
        "block": "Sonari",
        "bank_account": "1234567890",
        "ifsc": "INVALID_IFSC",
    }, doc_path="test.pdf")
    assert any("IFSC" in e for e in errors_ifsc)

    # 3. Invalid bank account (too short)
    errors_bank, _ = portal.validate_application({
        "applicant_name": "Elder Citizen",
        "dob": "01/01/1950",
        "gender": "Male",
        "marital_status": "Married",
        "village": "Sonari Gaon",
        "block": "Sonari",
        "bank_account": "12345",
        "ifsc": "SBIN0003077",
    }, doc_path="test.pdf")
    assert any("bank account" in e.lower() for e in errors_bank)


def test_portal_admin_login_rejects_bad_credentials(client):
    """Admin portal login rejects incorrect password."""
    res = client.post("/admin", data={
        "username": "admin",
        "password": "wrong-password-attacker",
    }, follow_redirects=True)
    html = res.data.decode("utf-8")
    assert "invalid credentials" in html.lower()


def test_portal_admin_login_success(client):
    """Admin portal login succeeds with valid configured credentials."""
    with patch.object(portal, "ADMIN_PASSWORD", "correct-test-pass"):
        res = client.post("/admin", data={
            "username": "admin",
            "password": "correct-test-pass",
        }, follow_redirects=False)
        assert res.status_code == 302
        assert "/admin/dashboard" in res.headers.get("Location", "")


def test_portal_acknowledgment_pdf_generation(client):
    """PDF acknowledgment downloads successfully for valid application."""
    mock_app_row = (
        "SSP26448454", "Moheswar Barman", "9864012345", datetime(1955, 3, 10).date(),
        "Sonari Gaon", "Sonari", "3188021045678", "SBIN0003077", datetime.now(), "APPROVED"
    )

    with patch("app.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.side_effect = [(1,), (1,), mock_app_row]

        with client.session_transaction() as sess:
            sess["admin"] = True

        res = client.get("/ack/1.pdf")
        assert res.status_code == 200
        assert res.content_type == "application/pdf"
        assert res.data.startswith(b"%PDF-")


def test_portal_status_page(client):
    """Citizen status page renders login form."""
    res = client.get("/status")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "mobile" in html.lower()
