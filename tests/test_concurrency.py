# -*- coding: utf-8 -*-
"""Concurrency test: prove simultaneous applications for the same citizen create at most one active application."""

import threading
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

import app as portal
import api as api_module


def test_concurrent_active_application_submission(client, citizen_token):
    """Run two concurrent submissions for the same citizen mobile.
    Verify that exactly one succeeds (201) and the other is rejected (409)."""
    token, _ = citizen_token

    pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\ntrailer<</Root 1 0 R>>\nstartxref\n9\n%%EOF"
    b64 = "JVBERi0xLjQK"

    payload = {
        "applicant_name": "Rima Bora",
        "dob": "05/03/1956",
        "gender": "Female",
        "marital_status": "Widowed",
        "village": "Lakhipur",
        "block": "Sonari",
        "bank_account": "3188021045678",
        "ifsc": "SBIN0003077",
        "age_proof": {"content_b64": b64, "filename": "proof.pdf"},
    }

    # Simulate transactional database behavior with unique constraint on active mobile
    active_records = {} # mobile -> app_no
    lock = threading.Lock()

    def mock_active_app(cur, mobile):
        with lock:
            if mobile in active_records:
                return (1, active_records[mobile], "PENDING")
            return None

    def mock_create_app(cur, mobile, cleaned):
        with lock:
            if mobile in active_records:
                # Simulates database UniqueViolation from idx_active_app_mobile
                raise Exception("duplicate key value violates unique constraint idx_active_app_mobile")
            app_no = "SSP26999999"
            active_records[mobile] = app_no
            return (1, app_no)

    results = []

    def submit_application():
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json=payload)
        results.append(res.status_code)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch("app.get_db", return_value=mock_conn), \
         patch("app.active_application", side_effect=mock_active_app), \
         patch("app.create_application", side_effect=mock_create_app), \
         patch("app.send_sms"):
        t1 = threading.Thread(target=submit_application)
        t2 = threading.Thread(target=submit_application)

        t1.start()
        t2.start()
        t1.join()
        t2.join()


    assert len(results) == 2
    # Exactly one must succeed with 201, and the concurrent collision must receive 409
    assert sorted(results) == [201, 409], f"Unexpected concurrent outcomes: {results}"
    assert len(active_records) == 1
