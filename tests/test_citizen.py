# -*- coding: utf-8 -*-
"""Citizen workflow tests — unit, schema, and security."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

import app as portal
import oauth as oauth_as


def test_successful_application_validation():
    data = {
        "applicant_name": "Rima Bora",
        "dob": "05/03/1956",
        "gender": "Female",
        "marital_status": "Widowed",
        "village": "Lakhipur",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
    }
    errors, cleaned = portal.validate_application(data, "/tmp/doc.pdf")
    assert errors == []
    assert cleaned["applicant_name"] == "Rima Bora"
    assert cleaned["dob"] == datetime.strptime("05/03/1956", "%d/%m/%Y").date()
    assert cleaned["bank_account"] == "3188021045678"
    assert cleaned["ifsc"] == "SBIN0003077"


def test_underage_applicant():
    # 58 years old
    dob_58 = (portal.now_ist().date() - timedelta(days=58 * 365 + 10)).strftime("%d/%m/%Y")
    data = {
        "applicant_name": "Sunita Karmakar",
        "dob": dob_58,
        "village": "Sialmari",
        "block": "Sonari",
        "bank_account": "2011045567789",
        "ifsc": "SBIN0003077",
    }
    errors, _ = portal.validate_application(data, "/tmp/doc.pdf")
    assert any("60 years of age or above" in e for e in errors)


def test_invalid_dob():
    data = {
        "applicant_name": "Applicant",
        "dob": "1956-05-03", # Invalid format, portal requires DD/MM/YYYY
        "village": "Village",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
    }
    errors, _ = portal.validate_application(data, "/tmp/doc.pdf")
    assert any("date of birth must be given as DD/MM/YYYY" in e for e in errors)


def test_invalid_ifsc():
    data = {
        "applicant_name": "Applicant",
        "dob": "05/03/1956",
        "village": "Village",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN00307", # 10 chars, missing a digit
    }
    errors, _ = portal.validate_application(data, "/tmp/doc.pdf")
    assert any("IFSC must be 11 characters" in e for e in errors)


def test_invalid_bank_account():
    data = {
        "applicant_name": "Applicant",
        "dob": "05/03/1956",
        "village": "Village",
        "block": "Sonari",
        "bank_account": "12345", # Less than 9 digits
        "ifsc": "SBIN0003077",
    }
    errors, _ = portal.validate_application(data, "/tmp/doc.pdf")
    assert any("bank account number must be 9 to 18 digits" in e for e in errors)


def test_missing_age_proof():
    data = {
        "applicant_name": "Applicant",
        "dob": "05/03/1956",
        "village": "Village",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
    }
    errors, _ = portal.validate_application(data, "")
    assert any("age proof document is required" in e for e in errors)


def test_closed_application_window():
    data = {
        "applicant_name": "Applicant",
        "dob": "05/03/1956",
        "village": "Village",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
    }
    # Simulate current time past deadline
    with patch("app.now_ist", return_value=datetime(2030, 1, 1)):
        errors, _ = portal.validate_application(data, "/tmp/doc.pdf")
        assert any("application window has closed" in e for e in errors)


def test_duplicate_active_application(client, mock_db):
    conn, cur = mock_db
    cur.fetchone.return_value = (101, "SSP26123456", "PENDING")

    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "citizen:9864012345",
            "role": "citizen",
            "scope": "citizen",
            "aud": portal.config_public_base_url() + "/mcp/citizen",
        }
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": "Bearer valid_tok"},
                          json={
                              "applicant_name": "Rima Bora",
                              "dob": "05/03/1956",
                              "gender": "Female",
                              "marital_status": "Widowed",
                              "village": "Lakhipur",
                              "block": "Sonari",
                              "bank_account": "3188021045678",
                              "ifsc": "SBIN0003077",
                              "age_proof": {"content_b64": "JVBERi0xLjQK...", "filename": "doc.pdf"},
                          })
        assert res.status_code == 409
        data = res.get_json()
        assert data["error"] == "active_application_exists"


def test_unauthorized_status_access(client):
    # Missing Bearer token
    res = client.get("/api/citizen/application")
    assert res.status_code == 401
    assert res.get_json()["error"] == "missing_bearer_token"


def test_expired_oauth_token(client):
    with patch("oauth.verify_token", return_value=None):
        res = client.get("/api/citizen/application",
                         headers={"Authorization": "Bearer expired_or_bad_token"})
        assert res.status_code == 401
        assert res.get_json()["error"] == "invalid_token"


def test_invalid_oauth_audience(client):
    # Audience was minted for officer instead of citizen
    with patch("oauth.verify_token", return_value=None):
        res = client.get("/api/citizen/application",
                         headers={"Authorization": "Bearer officer_audience_token"})
        assert res.status_code == 401


def test_invalid_oauth_scope(client):
    with patch("oauth.verify_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "officer:admin",
            "role": "officer",
            "scope": "officer",
        }
        res = client.get("/api/citizen/application",
                         headers={"Authorization": "Bearer officer_token"})
        assert res.status_code == 403
        assert res.get_json()["error"] == "insufficient_scope"


def test_invalid_otp(client, mock_db):
    conn, cur = mock_db
    # Mock OTP row in database: code '123456'
    cur.fetchone.return_value = ("123456", datetime.now())

    with patch("oauth._load_request") as mock_req:
        mock_req.return_value = {
            "id": "rid123",
            "role": "citizen",
            "pending_mobile": "9864012345",
            "params": {"client_name": "Test"},
            "subject": None
        }
        # Citizen submits wrong OTP '999999'
        res = client.post("/oauth/login?rid=rid123", data={"otp": "999999"})
        assert res.status_code == 200
        assert b"Invalid or expired OTP" in res.data
