# -*- coding: utf-8 -*-
"""Privacy verification tests: ensure no unmasked PII appears in rendered dashboard HTML or source."""

import re
import pytest
from bs4 import BeautifulSoup


def test_eval_dashboard_masks_phone_numbers(client):
    """Rendered dashboard must contain NO raw 10-digit Indian mobile numbers."""
    res = client.get("/eval-dashboard")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    # Match raw 10-digit mobile numbers starting with 6, 7, 8, or 9
    raw_mobile_matches = re.findall(r"\b[6-9]\d{9}\b", html)
    assert len(raw_mobile_matches) == 0, f"Leaked unmasked mobile numbers in dashboard: {raw_mobile_matches}"


def test_eval_dashboard_masks_bank_accounts(client):
    """Rendered dashboard must contain NO raw 9 to 18-digit bank account numbers."""
    res = client.get("/eval-dashboard")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    # Bank account numbers in personas are 11-14 digits like 3188021045678, 9130200554433
    raw_bank_matches = re.findall(r"\b\d{11,18}\b", html)
    # Filter out timestamps like 20261029... if any
    suspicious_accounts = [b for b in raw_bank_matches if not b.startswith("2026") and not b.startswith("2025")]
    assert len(suspicious_accounts) == 0, f"Leaked unmasked bank accounts in dashboard: {suspicious_accounts}"


def test_eval_dashboard_no_secret_leakage(client):
    """Ensure no raw tokens, OTPs, or passwords appear in dashboard page source."""
    res = client.get("/eval-dashboard")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    assert "Bearer eyJ" not in html, "Raw JWT access token leaked in dashboard HTML!"
    assert not re.search(r"(?i)\botp\s*(?:is|:|=)\s*\d{6}\b", html), "Raw OTP code leaked in dashboard HTML!"


def test_mask_pii_text_adversarial_inputs():
    """Unit test mask_pii_text across adversarial PII representations."""
    from dashboard import mask_pii_text

    # 1. Mobile embedded in filename
    masked = mask_pii_text("uploads/9864012345_ageproof_a1b2c3d4.pdf")
    assert "9864012345" not in masked
    assert "98640XXXXX" in masked

    # 2. +91 prefix and space-separated mobile
    masked_intl = mask_pii_text("Contact citizen at +91 98640 12345 or 98640-12345")
    assert "12345" not in masked_intl
    assert "XXXXX" in masked_intl

    # 3. 12-digit Aadhaar number
    masked_aadhaar = mask_pii_text("Aadhaar card: 1234 5678 9012 presented")
    assert "5678" not in masked_aadhaar
    assert "XXXX" in masked_aadhaar

    # 4. API keys
    masked_api = mask_pii_text("Using key sk-proj-1234567890abcdef1234567890 for evaluation")
    assert "sk-proj-1234567890abcdef1234567890" not in masked_api
    assert "[MASKED_API_KEY]" in masked_api

    # 5. Opaque Bearer token (non-JWT)
    masked_opaque = mask_pii_text("Authorization: Bearer secret-token-opaque-value-12345")
    assert "secret-token-opaque-value-12345" not in masked_opaque
    assert "[MASKED_TOKEN]" in masked_opaque

    # 6. Credentials in JSON / logs
    masked_cred = mask_pii_text('{"db_user": "sewasetu", "password": "supersecretpassword123"}')
    assert "supersecretpassword123" not in masked_cred
    assert "[MASKED_CREDENTIAL]" in masked_cred

