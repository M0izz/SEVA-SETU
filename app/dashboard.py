import json
import os
import re

from flask import Blueprint, render_template

dash = Blueprint("dash", __name__)


def mask_pii_text(text: str) -> str:
    """Mask mobile numbers, bank accounts, tokens, and OTPs in text."""
    if not isinstance(text, str):
        return text
    # Mask 10-digit Indian mobile numbers: e.g. 9864012345 -> 98640XXXXX
    text = re.sub(r"\b([6-9]\d{4})(\d{5})\b", r"\1XXXXX", text)
    # Mask bank accounts (9 to 18 digits): e.g. 3188021045678 -> 31XXXXXX5678
    def _mask_bank(match):
        val = match.group(0)
        # Avoid masking dates like 19560305 (8 digits) or timestamps
        if len(val) >= 9 and len(val) <= 18:
            return val[:2] + "XXXXXX" + val[-4:]
        return val
    text = re.sub(r"\b\d{9,18}\b", _mask_bank, text)
    # Mask OTP occurrences
    text = re.sub(r"(?i)\b(otp\s*(?:is|:|=)\s*)(\d{6})\b", r"\1XXXXXX", text)
    # Mask Bearer tokens / JWTs
    text = re.sub(r"Bearer\s+eyJ[A-Za-z0-9-_.]+", "Bearer [MASKED_TOKEN]", text)
    return text


def sanitize_payload(obj):
    """Recursively mask sensitive identifiers across dictionaries and lists."""
    if isinstance(obj, dict):
        return {k: sanitize_payload(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_payload(item) for item in obj]
    elif isinstance(obj, str):
        return mask_pii_text(obj)
    return obj


@dash.app_template_filter("mask_pii")
def jinja_mask_pii_filter(s):
    return mask_pii_text(str(s)) if s else ""


def _results_path():
    return (os.environ.get("SS_EVAL_RESULTS")
            or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "evals", "results.json"))


@dash.get("/eval-dashboard")
def eval_dashboard():
    path = _results_path()
    data = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw_data = json.load(fh)
                data = sanitize_payload(raw_data)
        except Exception:
            data = None
    if not data:
        return render_template("eval_dashboard.html", data=None,
                               results_path=path), 200
    return render_template("eval_dashboard.html", data=data, results_path=path)

