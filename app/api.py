# -*- coding: utf-8 -*-
"""
Sewa Setu citizen/officer JSON API — the layer the MCP servers call.

Design rules (from the Director's note):
  - An AI-assisted application follows EXACTLY the same rules and validations
    as the web form: every write goes through validate_application() and
    create_application() in app.py. No channel can admit what another would
    refuse. The portal remains the system of record.
  - The agent channel is rolled out to a SUBSET of blocks first (pilot),
    exactly as the Director asked. The web form is untouched.
  - Every endpoint requires a Bearer access token minted by this portal's
    OAuth server, with audience = the canonical URL of the MCP server for
    that role, and role/scope matching the endpoint. A citizen token can
    never call an officer endpoint and vice versa.
"""

import base64
import ipaddress
import json
import os
import re
import secrets
import socket
import urllib.parse
from datetime import datetime, timedelta

import requests as http
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.poolmanager import PoolManager
from urllib3.util import connection
from flask import Blueprint, jsonify, request, abort

import app as portal
import oauth as oauth_as

api = Blueprint("api", __name__)

# The agent channel's pilot blocks (Director: "agent only work for a subset of
# blocks"). Sonari is where the initial application happened; Namti
# joins the pilot.
AGENT_PILOT_BLOCKS = ("Sonari", "Namti")

UPLOAD_EXT_WHITELIST = ("pdf", "jpg", "jpeg", "png")
MAX_DOC_BYTES = 5 * 1024 * 1024
MAX_B64_LEN = (MAX_DOC_BYTES * 4 // 3) + 2048

BLOCKED_INTERNAL_HOSTS = {
    "localhost", "db", "postgres", "smsgw", "app", "scheduler",
    "caddy", "redis", "citizen-mcp", "officer-mcp", "127.0.0.1", "::1"
}

# Trusted block assignments for departmental officers
DEFAULT_OFFICER_BLOCKS = {
    "admin": "Sonari",
    "officer_sonari": "Sonari",
    "officer_namti": "Namti",
    "block_officer_sonari": "Sonari",
}



def _create_pinned_pool_classes(pinned_ip: str):
    """Create HTTP and HTTPS connection pool classes that bind directly
    to the pre-validated IP address, guaranteeing that no secondary DNS
    lookup occurs between validation and TCP socket connection (anti-DNS-rebinding).
    TLS SNI and certificate verification are preserved using the original hostname."""
    class PinnedHTTPConnection(HTTPConnection):
        def _new_conn(self):
            extra_kw = {}
            if self.source_address:
                extra_kw["source_address"] = self.source_address
            if self.socket_options:
                extra_kw["socket_options"] = self.socket_options
            return connection.create_connection(
                (pinned_ip, self.port),
                self.timeout,
                **extra_kw
            )

    class PinnedHTTPSConnection(HTTPSConnection):
        def _new_conn(self):
            extra_kw = {}
            if self.source_address:
                extra_kw["source_address"] = self.source_address
            if self.socket_options:
                extra_kw["socket_options"] = self.socket_options
            return connection.create_connection(
                (pinned_ip, self.port),
                self.timeout,
                **extra_kw
            )

    class PinnedHTTPPool(HTTPConnectionPool):
        ConnectionCls = PinnedHTTPConnection

    class PinnedHTTPSPool(HTTPSConnectionPool):
        ConnectionCls = PinnedHTTPSConnection

    return PinnedHTTPPool, PinnedHTTPSPool


class PinnedIPAdapter(HTTPAdapter):
    """Requests adapter enforcing transport-level IP pinning to prevent DNS rebinding."""

    def __init__(self, pinned_ip: str, *args, **kwargs):
        self.pinned_ip = pinned_ip
        self.http_pool_cls, self.https_pool_cls = _create_pinned_pool_classes(pinned_ip)
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs
        )
        self.poolmanager.pool_classes_by_scheme = {
            "http": self.http_pool_cls,
            "https": self.https_pool_cls,
        }




def _mcp_audience(role):
    return portal.config_public_base_url() + "/mcp/" + role


def _authenticate(role):
    """Verify the Bearer token for this endpoint's role. Returns (claims, None)
    or (None, (response, status))."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, (jsonify({"error": "missing_bearer_token",
                               "hint": "sign in at the portal; tokens are issued "
                                       "by its OAuth server"}), 401)
    claims = oauth_as.verify_token(auth[7:], audience=_mcp_audience(role))
    if not claims:
        return None, (jsonify({"error": "invalid_token",
                               "detail": "token must be issued by this portal for "
                                         "audience " + _mcp_audience(role)}), 401)
    if claims.get("role") != role or claims.get("scope") != role:
        return None, (jsonify({"error": "insufficient_scope",
                               "detail": "this endpoint needs the %r role" % role}), 403)
    return claims, None


def _subject_value(claims, role):
    return claims.get("sub", "").split(":", 1)[-1]


# ---------------------------------------------------------------------------
# Citizen endpoints
# ---------------------------------------------------------------------------

@api.get("/api/citizen/me")
def citizen_me():
    claims, err = _authenticate("citizen")
    if err:
        return err
    mobile = _subject_value(claims, "citizen")
    conn = portal.get_db()
    cur = conn.cursor()
    row = portal.active_application(cur, mobile)
    cur.close(); conn.close()
    return jsonify({
        "mobile": mobile,
        "active_application": ({"application_no": row[1], "status": row[2]} if row else None),
        "pilot_blocks": list(AGENT_PILOT_BLOCKS),
    })


@api.get("/api/citizen/eligibility")
def citizen_eligibility():
    """Read-only pre-check. Same arithmetic the form's validation will apply,
    plus the citizen's current application state. Changes nothing."""
    claims, err = _authenticate("citizen")
    if err:
        return err
    mobile = _subject_value(claims, "citizen")
    dob = portal.parse_dob(request.args.get("dob", ""))
    out = {"date_of_birth": request.args.get("dob", ""), "min_age": portal.MIN_AGE}
    if dob is None:
        out.update({"age_ok": False,
                    "detail": "date of birth must be given as DD/MM/YYYY"})
    else:
        age = portal.age_on(dob, portal.now_ist().date())
        out.update({"age": age, "age_ok": age >= portal.MIN_AGE})
    window_open = portal.deadline_remaining() is not None
    out["window_open"] = window_open
    if window_open:
        out["hours_left_to_apply"] = portal.deadline_remaining()
    conn = portal.get_db()
    cur = conn.cursor()
    row = portal.active_application(cur, mobile)
    cur.close(); conn.close()
    out["existing_application"] = ({"application_no": row[1], "status": row[2]}
                                   if row else None)
    out["may_apply"] = bool(out.get("age_ok")) and window_open and not row
    return jsonify(out)


def _detect_and_validate_format(content: bytes) -> tuple[str | None, str | None]:
    """Validate document bytes against magic signatures and reject dangerous/ambiguous payloads.
    Returns (detected_ext, error_detail)."""
    if not content or len(content) < 8:
        return None, "file is empty or too small"
    if len(content) > MAX_DOC_BYTES:
        return None, "file is larger than 5 MB"

    head_1k = content[:1024].lower()
    dangerous_signatures = (
        b"<html", b"<!doctype html", b"<script", b"<?xml", b"<svg",
        b"<?php", b"<% ", b"#!/bin/", b"#!/usr/bin/", b"mz", b"\x7felf"
    )
    for sig in dangerous_signatures:
        if sig in head_1k:
            return None, "executable, script, SVG, or HTML files are not permitted"

    if content.startswith(b"%PDF-"):
        return "pdf", None
    elif content.startswith(b"\xff\xd8\xff"):
        return "jpg", None
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", None

    return None, "age proof must be a valid PDF, JPG or PNG document (unrecognized signature)"


def _is_safe_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if (ip.is_private or ip.is_loopback or ip.is_link_local or
                ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
        if str(ip) in ("169.254.169.254", "fd00:ec2::254"):
            return False
        return True
    except ValueError:
        return False


def _is_allowed_eval_host(hostname: str, port: int) -> bool:
    allowed = os.environ.get("ALLOWED_DOC_HOSTS", "").strip()
    if not allowed:
        return False
    allowed_list = [h.strip().lower() for h in allowed.split(",") if h.strip()]
    target = f"{hostname.lower()}:{port}"
    return hostname.lower() in allowed_list or target in allowed_list


def _validate_and_fetch_url(url: str) -> tuple[bytes | None, str | None]:
    """Safely validate and fetch remote document with strict SSRF defense."""
    url = (url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return None, "malformed URL"

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None, "age_proof url must use HTTP or HTTPS"

    if parsed.username or parsed.password:
        return None, "URLs with embedded credentials are not permitted"

    hostname = parsed.hostname
    if not hostname:
        return None, "URL must include a valid hostname"

    hostname_clean = hostname.lower().strip("[]")
    port = parsed.port or (443 if scheme == "https" else 80)

    # Check evaluation bypass (only active when ALLOWED_DOC_HOSTS is explicitly configured)
    is_eval_allowed = _is_allowed_eval_host(hostname_clean, port)

    if not is_eval_allowed:
        if hostname_clean in BLOCKED_INTERNAL_HOSTS or hostname_clean.endswith((".local", ".internal", ".localhost")):
            return None, "access to internal network addresses or services is prohibited"

        if port not in (80, 443, 8080, 8443):
            return None, "non-standard destination ports are prohibited"

        try:
            addr_info = socket.getaddrinfo(hostname_clean, port, type=socket.SOCK_STREAM)
            if not addr_info:
                return None, "could not resolve destination host"
            for addr in addr_info:
                ip_str = addr[4][0]
                if not _is_safe_ip(ip_str):
                    return None, "access to private, loopback, link-local, or internal IP addresses is prohibited"
        except socket.gaierror:
            return None, "destination host could not be resolved"
        except Exception:
            return None, "destination address validation failed"

        validated_ip = addr_info[0][4][0]
    else:
        # Evaluation bypass (only active when ALLOWED_DOC_HOSTS is explicitly configured)
        try:
            addr_info = socket.getaddrinfo(hostname_clean, port, type=socket.SOCK_STREAM)
            validated_ip = addr_info[0][4][0]
        except Exception:
            validated_ip = hostname_clean

    # Enforce transport-level IP pinning to eliminate DNS rebinding TOCTOU
    session = http.Session()
    adapter = PinnedIPAdapter(validated_ip)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        r = session.get(
            url,
            timeout=5,
            stream=True,
            allow_redirects=False,
            verify=True,
            headers={"User-Agent": "SewaSetu-Agent/1.0"}
        )
    except Exception:
        return None, "could not connect to the remote host"
    finally:
        session.close()

    if r.status_code in (301, 302, 303, 307, 308):
        return None, "URL redirects are not permitted for security reasons"

    if r.status_code != 200:
        return None, "age_proof url returned HTTP %d" % r.status_code

    chunks = []
    total = 0
    try:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_DOC_BYTES:
                    return None, "file is larger than 5 MB"
    except Exception:
        return None, "connection interrupted while reading file"

    return b"".join(chunks), None



def _save_age_proof(mobile, payload):
    """Store the age-proof document with strict byte signature and SSRF validation.
    Accepts either a URL or bounded base64 content. Returns (path, error_response_or_none)."""
    content = None
    if payload.get("content_b64"):
        raw_b64 = str(payload.get("content_b64")).strip()
        if len(raw_b64) > MAX_B64_LEN:
            return None, (jsonify({"error": "bad_age_proof",
                                   "detail": "file is larger than 5 MB"}), 400)
        clean_b64 = raw_b64.rstrip(".").strip()
        try:
            content = base64.b64decode(clean_b64, validate=True)
        except Exception:
            return None, (jsonify({"error": "bad_age_proof",
                                   "detail": "content_b64 is not valid base64"}), 400)

    elif payload.get("url"):
        fetched_content, fetch_err = _validate_and_fetch_url(payload.get("url"))
        if fetch_err:
            return None, (jsonify({"error": "bad_age_proof", "detail": fetch_err}), 400)
        content = fetched_content
    else:
        return None, (jsonify({"error": "bad_age_proof",
                                "detail": "provide age_proof url or content_b64"}), 400)

    detected_ext, sig_err = _detect_and_validate_format(content)
    if sig_err:
        return None, (jsonify({"error": "bad_age_proof", "detail": sig_err}), 400)

    os.makedirs(portal.UPLOAD_DIR, exist_ok=True)
    clean_mobile = re.sub(r"[^0-9]", "", str(mobile))
    safe_name = "%s_ageproof_%s.%s" % (clean_mobile, secrets.token_hex(4), detected_ext)
    path = os.path.join(portal.UPLOAD_DIR, safe_name)
    try:
        with open(path, "wb") as out:
            out.write(content)
    except Exception:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        return None, (jsonify({"error": "bad_age_proof",
                               "detail": "failed to store age proof document"}), 500)
    return path, None


@api.post("/api/citizen/apply")
def citizen_apply():
    """Submit (or dry-run) a pension application for the signed-in citizen.
    Same validation as the web form — via validate_application() — plus the
    agent-channel pilot-block restriction and database-level concurrency protection."""
    claims, err = _authenticate("citizen")
    if err:
        return err
    mobile = _subject_value(claims, "citizen")
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run"))

    block = (data.get("block") or "").strip()
    if block and block not in AGENT_PILOT_BLOCKS:
        return jsonify({
            "error": "block_not_in_agent_pilot",
            "detail": "the AI-assisted channel currently serves only these blocks: "
                      + ", ".join(AGENT_PILOT_BLOCKS)
                      + ". Please apply on the portal or at your block office for: " + block,
        }), 403

    doc_path, derr = _save_age_proof(mobile, data.get("age_proof") or {})
    if derr:
        return derr

    errors, cleaned = portal.validate_application(data, doc_path)
    if dry_run:
        # Clean up temporary upload file created during dry-run validation
        if doc_path and os.path.exists(doc_path):
            try:
                os.remove(doc_path)
            except Exception:
                pass
        return jsonify({"dry_run": True, "valid": not errors, "errors": errors,
                        "would_submit": cleaned if not errors else None})
    if errors:
        if doc_path and os.path.exists(doc_path):
            try:
                os.remove(doc_path)
            except Exception:
                pass
        return jsonify({"error": "validation_failed", "errors": errors}), 422

    conn = portal.get_db()
    cur = conn.cursor()
    existing = portal.active_application(cur, mobile)
    if existing:
        cur.close(); conn.close()
        if doc_path and os.path.exists(doc_path):
            try:
                os.remove(doc_path)
            except Exception:
                pass
        return jsonify({"error": "active_application_exists",
                        "application_no": existing[1], "status": existing[2],
                        "detail": "this mobile number already holds an active "
                                  "application; a PENDING one can be withdrawn "
                                  "from the status portal first"}), 409

    try:
        new_id, app_no = portal.create_application(cur, mobile, cleaned)
        conn.commit()
    except Exception as e:
        conn.rollback()
        err_msg = str(e).lower()
        if "unique" in err_msg or "duplicate" in err_msg or "idx_active_app_mobile" in err_msg:
            existing = portal.active_application(cur, mobile)
            cur.close(); conn.close()
            if doc_path and os.path.exists(doc_path):
                try:
                    os.remove(doc_path)
                except Exception:
                    pass
            return jsonify({"error": "active_application_exists",
                            "application_no": existing[1] if existing else None,
                            "status": existing[2] if existing else "PENDING",
                            "detail": "this mobile number already holds an active application"}), 409
        cur.close(); conn.close()
        raise

    cur.close(); conn.close()
    portal.send_sms(mobile, "Sewa Setu: application %s received. Track at the status portal "
                            "with mobile no. and password (DOB as DDMMYYYY)." % app_no)
    return jsonify({"application_no": app_no, "status": "PENDING",
                    "submitted_at": portal.now_ist().isoformat(timespec="seconds")}), 201



@api.get("/api/citizen/application")
def citizen_application():
    claims, err = _authenticate("citizen")
    if err:
        return err
    mobile = _subject_value(claims, "citizen")
    conn = portal.get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, application_no, applicant_name, dob, village, block, "
                "bank_account, ifsc, status, submitted_at, decided_at, decided_by "
                "FROM applications WHERE mobile = %s ORDER BY submitted_at DESC LIMIT 1",
                (mobile,))
    row = cur.fetchone()
    audit = []
    if row:
        cur.execute("SELECT at, action, actor, note FROM audit_log "
                    "WHERE application_id = %s ORDER BY at ASC", (row[0],))
        audit = cur.fetchall()
    cur.close(); conn.close()
    if not row:
        return jsonify({"application": None})
    sla_left = None
    if row[8] == "PENDING" and row[9]:
        days = (portal.now_ist() - row[9]).days
        sla_left = max(0, portal.SLA_DAYS - days)
    return jsonify({
        "application": {
            "application_no": row[1], "applicant_name": row[2], "dob": str(row[3]),
            "village": row[4], "block": row[5], "bank_account": row[6], "ifsc": row[7],
            "status": row[8], "submitted_at": str(row[9]), "decided_at": str(row[10]),
            "decided_by": row[11],
            "pension_per_month": 250,
            "sla_days_left_if_pending": sla_left,
        },
        "audit": [{"at": str(a), "action": act, "actor": actor, "note": note}
                  for a, act, actor, note in audit],
    })


@api.post("/api/citizen/withdraw")
def citizen_withdraw():
    claims, err = _authenticate("citizen")
    if err:
        return err
    mobile = _subject_value(claims, "citizen")
    data = request.get_json(silent=True) or {}
    app_no = (data.get("application_no") or "").strip()
    conn = portal.get_db()
    cur = conn.cursor()
    q = ("UPDATE applications SET status = 'WITHDRAWN', decided_at = %s, "
         "decided_by = 'APPLICANT' WHERE mobile = %s AND status = 'PENDING' ")
    params = [portal.now_ist(), mobile]
    if app_no:
        q += "AND application_no = %s "
        params.append(app_no)
    q += "RETURNING application_no"
    cur.execute(q, params)
    row = cur.fetchone()
    if row:
        cur.execute("SELECT id FROM applications WHERE application_no = %s", (row[0],))
        app_id = cur.fetchone()[0]
        portal.write_audit(cur, app_id, "WITHDRAW", "applicant:%s" % mobile,
                           "withdrawn via AI-assisted channel")
        conn.commit()
    cur.close(); conn.close()
    if not row:
        return jsonify({"error": "nothing_to_withdraw",
                        "detail": "no PENDING application found for this mobile number"}), 409
    return jsonify({"withdrawn": row[0]})


# ---------------------------------------------------------------------------
# Officer endpoints & Block-Level Authorization
# ---------------------------------------------------------------------------

def _get_officer_block(username: str) -> str | None:
    """Retrieve trusted block assignment for an officer identity.
    Checks configured env mappings first, then trusted defaults, then queries database.
    Returns None if officer has no trusted block assignment."""
    env_blocks = os.environ.get("OFFICER_BLOCK_MAPPINGS")
    if env_blocks:
        try:
            mapping = json.loads(env_blocks)
            if username in mapping:
                return mapping[username]
        except Exception:
            pass

    if username in DEFAULT_OFFICER_BLOCKS:
        return DEFAULT_OFFICER_BLOCKS[username]

    try:
        conn = portal.get_db()
        cur = conn.cursor()
        cur.execute("SELECT block FROM officer_blocks WHERE username = %s", (username,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and isinstance(row[0], str) and row[0]:
            return row[0]

    except Exception:
        pass

    return None



@api.get("/api/officer/summary")
def officer_summary():
    claims, err = _authenticate("officer")
    if err:
        return err
    officer_user = _subject_value(claims, "officer")
    assigned_block = _get_officer_block(officer_user)
    if not assigned_block:
        return jsonify({"error": "unauthorized_block",
                        "detail": "officer has no assigned block"}), 403

    conn = portal.get_db()
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM applications WHERE block = %s GROUP BY status",
                (assigned_block,))
    by_status = dict(cur.fetchall())
    cur.execute("SELECT count(*) FROM applications WHERE status = 'PENDING' "
                "AND block = %s AND submitted_at < %s",
                (assigned_block, portal.now_ist() - timedelta(days=portal.SLA_DAYS)))
    overdue = cur.fetchone()[0]
    cur.execute("SELECT block, count(*) FROM applications WHERE status = 'PENDING' "
                "AND block = %s GROUP BY block", (assigned_block,))
    by_block = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({
        "officer": officer_user,
        "assigned_block": assigned_block,
        "by_status": by_status,
        "overdue_pending": overdue,
        "pending_by_block": [{"block": b, "count": c} for b, c in by_block],
        "sla_days": portal.SLA_DAYS
    })


@api.get("/api/officer/applications")
def officer_list():
    claims, err = _authenticate("officer")
    if err:
        return err
    officer_user = _subject_value(claims, "officer")
    assigned_block = _get_officer_block(officer_user)
    if not assigned_block:
        return jsonify({"error": "unauthorized_block",
                        "detail": "officer has no assigned block"}), 403

    status = request.args.get("status", "PENDING")
    mobile = request.args.get("mobile", "").strip()
    caller_block = request.args.get("block", "").strip()

    # If caller specifies a block that differs from assigned block, reject
    if caller_block and caller_block.lower() != assigned_block.lower():
        return jsonify({"error": "cross_block_denied",
                        "detail": "access denied: you are not authorized for block %s" % caller_block}), 403

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    q = ("SELECT id, application_no, applicant_name, mobile, village, block, status, "
         "submitted_at FROM applications WHERE block = %s")
    params = [assigned_block]
    if mobile:
        q += " AND mobile = %s"; params.append(mobile)
    if status and status != "ALL":
        q += " AND status = %s"; params.append(status)
    q += " ORDER BY submitted_at ASC LIMIT 50 OFFSET %s"
    params.append((page - 1) * 50)

    conn = portal.get_db()
    cur = conn.cursor()
    cur.execute(q, params)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({
        "officer": officer_user,
        "assigned_block": assigned_block,
        "page": page,
        "status": status,
        "applications": [{"id": r[0], "application_no": r[1],
                          "applicant_name": r[2], "mobile": r[3],
                          "village": r[4], "block": r[5], "status": r[6],
                          "submitted_at": str(r[7])} for r in rows]
    })


def _find_application(cur, ref):
    """Accepts an application_no (SSP...) or a numeric id."""
    if re.fullmatch(r"\d+", str(ref)):
        cur.execute("SELECT id, application_no, applicant_name, mobile, dob, gender, "
                    "marital_status, husband_name, village, block, bank_account, ifsc, "
                    "doc_path, status, submitted_at, decided_at, decided_by "
                    "FROM applications WHERE id = %s", (int(ref),))
    else:
        cur.execute("SELECT id, application_no, applicant_name, mobile, dob, gender, "
                    "marital_status, husband_name, village, block, bank_account, ifsc, "
                    "doc_path, status, submitted_at, decided_at, decided_by "
                    "FROM applications WHERE application_no = %s", (str(ref),))
    return cur.fetchone()


@api.get("/api/officer/application/<ref>")
def officer_application(ref):
    claims, err = _authenticate("officer")
    if err:
        return err
    officer_user = _subject_value(claims, "officer")
    assigned_block = _get_officer_block(officer_user)
    if not assigned_block:
        return jsonify({"error": "unauthorized_block",
                        "detail": "officer has no assigned block"}), 403

    conn = portal.get_db()
    cur = conn.cursor()
    row = _find_application(cur, ref)
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "not_found"}), 404

    # Verify block authorization (row[9] is block)
    app_block = row[9]
    if app_block != assigned_block:
        cur.close(); conn.close()
        return jsonify({"error": "cross_block_denied",
                        "detail": "access denied: application belongs to another block"}), 403

    cur.execute("SELECT at, action, actor, note FROM audit_log "
                "WHERE application_id = %s ORDER BY at ASC", (row[0],))
    audit = cur.fetchall()
    cur.close(); conn.close()

    keys = ["id", "application_no", "applicant_name", "mobile", "dob", "gender",
            "marital_status", "husband_name", "village", "block", "bank_account",
            "ifsc", "doc_path", "status", "submitted_at", "decided_at", "decided_by"]
    d = dict(zip(keys, row))
    d["dob"] = str(d["dob"])
    d["submitted_at"] = str(d["submitted_at"])
    d["decided_at"] = str(d["decided_at"])
    d["age_today"] = portal.age_on(row[4], portal.now_ist().date()) if row[4] else None
    return jsonify({"application": d,
                    "audit": [{"at": str(a), "action": act, "actor": actor, "note": note}
                              for a, act, actor, note in audit]})


@api.post("/api/officer/decide")
def officer_decide():
    claims, err = _authenticate("officer")
    if err:
        return err
    officer_user = _subject_value(claims, "officer")
    assigned_block = _get_officer_block(officer_user)
    if not assigned_block:
        return jsonify({"error": "unauthorized_block",
                        "detail": "officer has no assigned block"}), 403

    data = request.get_json(silent=True) or {}
    ref = data.get("application_no") or data.get("id")
    decision = (data.get("decision") or "").strip().upper()
    note = (data.get("reason") or "").strip()
    if decision not in ("APPROVED", "REJECTED"):
        return jsonify({"error": "bad_decision",
                        "detail": "decision must be APPROVED or REJECTED"}), 400
    if not note:
        return jsonify({"error": "reason_required",
                        "detail": "the Director requires a reason recorded against "
                                  "the officer's name for every decision"}), 422

    conn = portal.get_db()
    cur = conn.cursor()
    row = _find_application(cur, ref)
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "not_found"}), 404

    # Verify application belongs to officer's assigned block
    app_block = row[9]
    if app_block != assigned_block:
        cur.close(); conn.close()
        return jsonify({"error": "cross_block_denied",
                        "detail": "access denied: cannot decide application outside your assigned block"}), 403

    # Check status is PENDING (cannot decide already decided or deemed approved)
    if row[13] != "PENDING":
        cur.close(); conn.close()
        return jsonify({"error": "not_pending",
                        "detail": "only a PENDING application can be decided; "
                                  "current status is %s" % row[13]}), 409

    # Concurrency safe atomic update with status and block guards
    cur.execute("UPDATE applications SET status = %s, decided_at = %s, decided_by = %s "
                "WHERE id = %s AND status = 'PENDING' AND block = %s RETURNING application_no",
                (decision, portal.now_ist(), officer_user, row[0], assigned_block))
    upd = cur.fetchone()
    if upd:
        portal.write_audit(cur, row[0], decision, "officer:%s" % officer_user,
                           "Block: %s; Reason: %s" % (assigned_block, note))
        conn.commit()
    cur.close(); conn.close()

    if not upd:
        return jsonify({"error": "decision_conflict",
                        "detail": "application was already decided or status changed concurrently"}), 409

    return jsonify({"application_no": upd[0], "status": decision,
                    "decided_by": officer_user, "block": assigned_block, "reason": note})

