import json
import os
import re

from flask import Blueprint, render_template

dash = Blueprint("dash", __name__)


def mask_pii_text(text: str) -> str:
    """Mask mobile numbers, bank accounts, tokens, credentials, and OTPs in text."""
    if not isinstance(text, str):
        return text

    # Mask 10-digit Indian mobile numbers (including optional country code and spaces): e.g. 9864012345 -> 98640XXXXX
    text = re.sub(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{4})[\s-]?(\d{5})(?!\d)", r"\1XXXXX", text)

    # Mask 12-digit Aadhaar numbers: e.g. 1234 5678 9012 -> 1234 XXXX 9012
    text = re.sub(r"\b(\d{4})[\s-](\d{4})[\s-](\d{4})\b", r"\1 XXXX \3", text)

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

    # Mask Bearer tokens (any format)
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9\-_.~+/]+=*", "Bearer [MASKED_TOKEN]", text)

    # Mask standalone JWTs
    text = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "[MASKED_JWT_TOKEN]", text)

    # Mask API keys (e.g. sk-...)
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{20,}\b", "[MASKED_API_KEY]", text)

    # Mask sensitive credential keys in JSON / log text
    text = re.sub(r'(?i)(["\']?(?:password|secret|api_key|token)["\']?\s*[:=]\s*["\'])([^"\'\s]+)(["\'])',
                  r'\1[MASKED_CREDENTIAL]\3', text)

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

