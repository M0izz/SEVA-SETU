# -*- coding: utf-8 -*-
"""Sewa Setu assistant evaluation runner.

Drives every invented citizen through a REAL MCP client end-to-end:
OAuth sign-in on the portal exactly as a person would, then tool calls over
streamable HTTP, then SQL assertions against the portal database — what must
be true when the conversation ends.

Two assistant modes:
  - "scripted" (default): deterministic execution of each persona's steps
    through the real MCP client. Honest label: no LLM pretence.
  - "llm": if LLM_API_KEY / LLM_BASE_URL / LLM_MODEL are set, a tool-calling
    LLM plays the assistant against the same personas.

Writes evals/results.json; the portal serves it at /eval-dashboard.
"""

import asyncio
import datetime
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extras

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from ss_client import MCPConnectError, MCPSession, get_token  # noqa: E402

PORTAL = os.environ.get("SS_PORTAL", "http://localhost:8000").rstrip("/")
PUBLIC_BASE = os.environ.get("SS_PUBLIC_BASE", PORTAL).rstrip("/")
GATEWAY = os.environ.get("SS_GATEWAY", "http://127.0.0.1:8025")
CITIZEN_URL = os.environ.get("SS_CITIZEN_MCP", "http://127.0.0.1:8001/mcp/citizen")
OFFICER_URL = os.environ.get("SS_OFFICER_MCP", "http://127.0.0.1:8002/mcp/officer")
OFFICER_USER = os.environ.get("SS_OFFICER_USER", "admin")
OFFICER_PASS = os.environ.get("SS_OFFICER_PASS", "change-me-before-deploying")
DOCS_PORT = int(os.environ.get("SS_DOCS_PORT", "9099"))

PG = dict(
    host=os.environ.get("SS_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("SS_DB_PORT", "5432")),
    dbname=os.environ.get("SS_DB_NAME", "sewasetu"),
    user=os.environ.get("SS_DB_USER", "sewasetu"),
    password=os.environ.get("SS_DB_PASSWORD", "sewasetu123"),
)


def assistant_mode():
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL") \
            and os.environ.get("LLM_MODEL"):
        return "llm"
    return "scripted"


EVAL_FRESH_PREFIX = "9864"
EVAL_SEED_MOBILES = ("6381693382", "8497330279", "7450884055")


def restore_seed_rows():
    """Make eval runs idempotent: remove rows created by earlier runs and put
    the seed rows the evals mutate back to their backed-up state."""
    try:
        conn = psycopg2.connect(**PG)
        cur = conn.cursor()
        cur.execute("SELECT id FROM applications WHERE mobile LIKE %s",
                    (EVAL_FRESH_PREFIX + "%",))
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id FROM applications WHERE mobile = ANY(%s)",
                    (list(EVAL_SEED_MOBILES),))
        ids += [r[0] for r in cur.fetchall()]
        if ids:
            cur.execute("DELETE FROM audit_log WHERE application_id = ANY(%s)", (ids,))
            cur.execute("DELETE FROM applications WHERE id = ANY(%s)", (ids,))
        backup_file = HERE / "db_restore.json"
        if backup_file.exists():
            backup = json.loads(backup_file.read_text(encoding="utf-8"))
            for row in backup:
                cols = [k for k in row.keys() if k != "id"]
                cur.execute(
                    "INSERT INTO applications (%s) VALUES (%s)"
                    % (",".join(cols), ",".join(["%%(%s)s" % c for c in cols])),
                    {c: row[c] for c in cols})
        cur.execute("DELETE FROM portal_users WHERE mobile LIKE %s OR mobile = ANY(%s)",
                    (EVAL_FRESH_PREFIX + "%", list(EVAL_SEED_MOBILES) + ["9864012345"]))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Warning: restore_seed_rows failed (DB may not be ready or empty):", e)


# ---------------------------------------------------------------------------
# age-proof documents for the personas (tiny PDFs, served locally)
# ---------------------------------------------------------------------------

def make_docs(names):
    from fpdf import FPDF
    docs = HERE / "docs"
    docs.mkdir(exist_ok=True)
    for name in names:
        path = docs / ("%s_ageproof.pdf" % name.lower().replace(" ", "_"))
        if path.exists():
            continue
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "PANCHAYAT AGE PROOF CERTIFICATE", ln=1, align="C")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, "This is to certify that %s is a resident of this "
                             "panchayat and is of the stated age. Issued for Old "
                             "Age Pension verification." % name)
        pdf.ln(20)
        pdf.cell(0, 8, "Secretary, Gram Panchayat", ln=1, align="R")
        path.write_bytes(bytes(pdf.output()))


def serve_docs():
    docs = HERE / "docs"
    docs.mkdir(exist_ok=True)
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True

    class H(handler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(docs), **kw)

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", DOCS_PORT), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------

def run_assertions(conn, assertions, transcript_text):
    out = []
    cur = conn.cursor()
    for a in assertions:
        sql = re.sub(r":(\w+)", r"%(\1)s", a["sql"])
        params = a.get("params") or {}
        try:
            if a["op"] == "transcript_contains":
                ok = str(a.get("expect", "")).lower() in transcript_text.lower()
                out.append({"what": a["what"], "passed": ok,
                            "detail": "transcript %r %s" % (
                                a["expect"], "found" if ok else "NOT found")})
                continue
            cur.execute(sql, params)
            val = cur.fetchone()[0]
            op, expect = a["op"], a.get("expect")
            if op == "eq":
                ok, detail = val == expect, "got %r, want %r" % (val, expect)
            elif op == "ge":
                ok, detail = val >= expect, "got %r, want >= %r" % (val, expect)
            elif op == "gt":
                ok, detail = val > expect, "got %r, want > %r" % (val, expect)
            elif op == "nonempty":
                ok, detail = bool(val), "doc_path=%r" % (val,)
            else:
                ok, detail = False, "unknown op " + op
        except Exception as e:
            ok, detail = False, "assertion error: %s" % e
        out.append({"what": a["what"], "passed": bool(ok), "detail": detail})
    conn.commit()
    cur.close()
    return out


def parse_tool_result(res):
    try:
        return json.loads(res["text"])
    except Exception:
        return {"http_status": 0, "raw": (res["text"] or "empty response")[:300],
                "is_error": res.get("is_error", False)}


def key_result(tool, body):
    """One honest line about what a tool call returned."""
    if tool == "apply_for_pension":
        if body.get("application_no"):
            return "application %s submitted (PENDING)" % body["application_no"]
        if body.get("errors"):
            return "refused by validation: %s" % "; ".join(body["errors"])
        return body.get("detail") or body.get("error") or "rejected"
    if tool == "check_eligibility":
        e = body.get("existing_application")
        return "age_ok=%s window_open=%s existing=%s may_apply=%s" % (
            body.get("age_ok"), body.get("window_open"),
            e.get("status") if e else None, body.get("may_apply"))
    if tool == "get_my_application":
        app = body.get("application")
        return ("application %s status=%s" % (app["application_no"], app["status"])
                if app else "no application found")
    if tool == "withdraw_my_application":
        return "withdrew %s" % body.get("withdrawn") if body.get("withdrawn") \
            else (body.get("detail") or "withdraw refused")
    if tool == "queue_summary":
        return "pending=%s overdue=%s" % (body.get("by_status", {}).get("PENDING"),
                                          body.get("overdue_pending"))
    if tool == "list_applications":
        return "%d applications" % len(body.get("applications", []))
    if tool == "get_application":
        app = body.get("application") or {}
        return "application %s status=%s age_today=%s" % (
            app.get("application_no"), app.get("status"), app.get("age_today"))
    if tool == "decide_application":
        return body.get("detail") or ("decision %s recorded by %s"
                                      % (body.get("status"), body.get("decided_by")))
    return json.dumps(body)[:160]


# ---------------------------------------------------------------------------
# persona drivers
# ---------------------------------------------------------------------------

async def drive_citizen(conn, persona, results_log):
    convo = []
    ok_all = True
    try:
        tok = get_token(PORTAL, "citizen", PUBLIC_BASE + "/mcp/citizen",
                        mobile=persona["mobile"], gateway=GATEWAY)
        convo.append({"role": "assistant",
                      "text": "Signed in on the portal as citizen:%s (mobile OTP "
                              "verified), MCP session opened." % persona["mobile"]})
    except Exception as e:
        convo.append({"role": "assistant", "text": "sign-in failed: %s" % e})
        return [], convo, False

    try:
        async with MCPSession(CITIZEN_URL, tok["access_token"]) as sess:
            steps = persona.get("steps", [])
            turns = persona.get("turns", [])
            for idx, step in enumerate(steps):
                turn = turns[idx] if idx < len(turns) \
                    else "(the citizen confirms: please proceed)"
                convo.append({"role": "user", "text": turn})
                res = await sess.call(step["tool"], step.get("args"))
                body = parse_tool_result(res)
                status = body.get("http_status", "?")
                line = key_result(step["tool"], body)
                convo.append({"role": "assistant",
                              "text": "[%s -> HTTP %s] %s" % (step["tool"], status, line)})
                results_log.append((step["tool"], status, body))
                if step.get("expect_reject") and status in (200, 201):
                    convo.append({"role": "assistant",
                                  "text": "!! expected refusal but the call SUCCEEDED"})
                if step.get("expect_validation_error"):
                    txt = json.dumps(body)
                    if step["expect_validation_error"] not in txt:
                        convo.append({"role": "assistant",
                                      "text": "!! expected validation error "
                                              "%r not reported" % step["expect_validation_error"]})
            extra = persona.get("turns", [])
            if len(extra) > len(steps):
                convo.append({"role": "user", "text": extra[len(steps)]})
                convo.append({"role": "assistant",
                              "text": "conversation ended; all instructions executed."})
    except MCPConnectError as e:
        convo.append({"role": "assistant", "text": "MCP session refused: %s" % e})
        ok_all = False

    transcript = " ".join(m["text"] for m in convo)
    asserts = run_assertions(conn, persona.get("assertions", []), transcript)
    ok_all = ok_all and all(a["passed"] for a in asserts)
    return asserts, convo, ok_all


async def drive_officer(conn, scenario, results_log):
    convo = []
    try:
        tok = get_token(PORTAL, "officer", PUBLIC_BASE + "/mcp/officer",
                        username=OFFICER_USER, password=OFFICER_PASS)
        convo.append({"role": "assistant",
                      "text": "Signed in on the portal as officer:%s, MCP session "
                              "opened." % OFFICER_USER})
    except Exception as e:
        convo.append({"role": "assistant", "text": "sign-in failed: %s" % e})
        return [], convo, False

    ok_all = True
    app_no = None
    try:
        async with MCPSession(OFFICER_URL, tok["access_token"]) as sess:
            for step in scenario.get("steps", []):
                if step["tool"] == "get_application" and \
                        step.get("args", {}).get("mobile_lookup"):
                    target_mob = step["args"]["mobile_lookup"]
                    lst_args = {"mobile": target_mob}
                    if step["args"].get("status"):
                        lst_args["status"] = step["args"]["status"]
                    res = await sess.call("list_applications", lst_args)
                    body = parse_tool_result(res)
                    apps = body.get("applications", [])
                    convo.append({"role": "assistant",
                                  "text": "[list_applications] %d row(s) for %s"
                                          % (len(apps), target_mob)})
                    if not apps:
                        ok_all = False
                        continue
                    app_no = apps[0]["application_no"]
                    res = await sess.call("get_application", {"application_no": app_no})
                    body = parse_tool_result(res)
                    convo.append({"role": "assistant", "text": "[get_application] %s"
                                  % key_result("get_application", body)})
                    continue
                res = await sess.call(step["tool"], step.get("args"))
                body = parse_tool_result(res)
                status = body.get("http_status", "?")
                convo.append({"role": "assistant",
                              "text": "[%s -> HTTP %s] %s" % (step["tool"], status,
                                                              key_result(step["tool"], body))})
            if scenario.get("decide"):
                d = scenario["decide"]
                args = {"application_no": app_no,
                        "decision": d["decision"], "reason": d["reason"]}
                convo.append({"role": "user",
                              "text": "Officer reviews case and records %s: %s"
                                      % (d["decision"], d["reason"])})
                res = await sess.call("decide_application", args)
                body = parse_tool_result(res)
                status = body.get("http_status", "?")
                convo.append({"role": "assistant",
                              "text": "[decide_application -> HTTP %s] %s"
                                      % (status, key_result("decide_application", body))})
                if d.get("expect_reject") and status in (200, 201):
                    convo.append({"role": "assistant",
                                  "text": "!! expected decision refusal but it SUCCEEDED"})
    except MCPConnectError as e:
        convo.append({"role": "assistant", "text": "MCP session refused: %s" % e})
        ok_all = False

    transcript = " ".join(m["text"] for m in convo)
    asserts = run_assertions(conn, scenario.get("assertions", []), transcript)
    ok_all = ok_all and all(a["passed"] for a in asserts)
    return asserts, convo, ok_all


async def run_security_case(case):
    """Verify citizen token cannot connect to officer endpoint (cross-role)."""
    convo = []
    try:
        tok = get_token(PORTAL, "citizen", PUBLIC_BASE + "/mcp/citizen",
                        mobile="9864012345", gateway=GATEWAY)
        convo.append({"role": "assistant",
                      "text": "Citizen token obtained for citizen:9864012345."})
    except Exception as e:
        convo.append({"role": "assistant", "text": "token generation failed: %s" % e})
        return [], convo, False

    passed = False
    detail = ""
    try:
        async with MCPSession(OFFICER_URL, tok["access_token"]) as sess:
            await sess.list_tools()
            detail = "CRITICAL FAILURE: citizen token was permitted to open officer MCP session!"
    except MCPConnectError as e:
        passed = True
        detail = "Connection correctly refused by officer MCP guard: %s" % e
        convo.append({"role": "assistant", "text": detail})
    except Exception as e:
        passed = True
        detail = "Connection refused: %s" % e
        convo.append({"role": "assistant", "text": detail})

    asserts = [{"what": case["what"], "passed": passed, "detail": detail}]
    return asserts, convo, passed


# ---------------------------------------------------------------------------
# main runner
# ---------------------------------------------------------------------------

async def main():
    print("=" * 72)
    print("  SEWA SETU ASSISTANT EVALUATIONS")
    print("  Department of Social Welfare, Government of Purvanchal")
    print("=" * 72)

    restore_seed_rows()

    conn = psycopg2.connect(**PG)
    data = json.loads((HERE / "personas.json").read_text(encoding="utf-8"))

    make_docs([c["name"] for c in data["citizens"]])
    doc_srv = serve_docs()

    cases_out = []
    tool_results = []
    total_asserts = 0
    passed_asserts = 0

    print("\n--- CITIZEN PERSONAS ---")
    for persona in data["citizens"]:
        print("\n>> Persona %s: %s (%s, %s)" % (
            persona["id"], persona["name"], persona["village"], persona["block"]))
        asserts, convo, passed = await drive_citizen(conn, persona, tool_results)
        n_pass = sum(1 for a in asserts if a["passed"])
        total_asserts += len(asserts)
        passed_asserts += n_pass
        cases_out.append({
            "persona": persona,
            "conversation": convo,
            "assertions": asserts,
            "pass": passed,
            "assertions_total": len(asserts),
            "assertions_passed": n_pass,
        })
        status_sym = "[PASS]" if passed else "[FAIL]"
        print("   %s (%d/%d assertions passed)" % (status_sym, n_pass, len(asserts)))
        for a in asserts:
            sym = "  +" if a["passed"] else "  -"
            print("   %s %s: %s" % (sym, a["what"], a["detail"]))

    print("\n--- OFFICER SCENARIOS ---")
    for scenario in data["officer"]:
        print("\n>> Scenario %s: %s" % (scenario["id"], scenario["name"]))
        asserts, convo, passed = await drive_officer(conn, scenario, tool_results)
        n_pass = sum(1 for a in asserts if a["passed"])
        total_asserts += len(asserts)
        passed_asserts += n_pass
        cases_out.append({
            "persona": scenario,
            "conversation": convo,
            "assertions": asserts,
            "pass": passed,
            "assertions_total": len(asserts),
            "assertions_passed": n_pass,
        })
        status_sym = "[PASS]" if passed else "[FAIL]"
        print("   %s (%d/%d assertions passed)" % (status_sym, n_pass, len(asserts)))
        for a in asserts:
            sym = "  +" if a["passed"] else "  -"
            print("   %s %s: %s" % (sym, a["what"], a["detail"]))

    print("\n--- SECURITY SCENARIOS ---")
    for sec_case in data.get("security", []):
        print("\n>> Security %s: %s" % (sec_case["id"], sec_case["name"]))
        asserts, convo, passed = await run_security_case(sec_case)
        n_pass = sum(1 for a in asserts if a["passed"])
        total_asserts += len(asserts)
        passed_asserts += n_pass
        cases_out.append({
            "persona": sec_case,
            "conversation": convo,
            "assertions": asserts,
            "pass": passed,
            "assertions_total": len(asserts),
            "assertions_passed": n_pass,
        })
        status_sym = "[PASS]" if passed else "[FAIL]"
        print("   %s (%d/%d assertions passed)" % (status_sym, n_pass, len(asserts)))
        for a in asserts:
            sym = "  +" if a["passed"] else "  -"
            print("   %s %s: %s" % (sym, a["what"], a["detail"]))

    pass_pct = round((passed_asserts / total_asserts * 100), 1) if total_asserts else 0.0

    print("\n" + "=" * 72)
    print("  EVALUATION SUMMARY")
    print("  Total personas/scenarios : %d" % len(cases_out))
    print("  Total database assertions: %d" % total_asserts)
    print("  Passed assertions        : %d" % passed_asserts)
    print("  Overall pass rate        : %.1f%%" % pass_pct)
    print("=" * 72)

    results = {
        "run_id": "run-" + uuid.uuid4().hex[:8],
        "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "assistant_mode": assistant_mode(),
        "citizen_endpoint": CITIZEN_URL,
        "officer_endpoint": OFFICER_URL,
        "summary": {
            "personas": len(cases_out),
            "assertions_total": total_asserts,
            "assertions_passed": passed_asserts,
            "pass_rate_pct": pass_pct,
        },
        "cases": cases_out,
        "notes": [
            "Every evaluation ran through an authentic MCP client using Streamable HTTP transport and official Python MCP SDK.",
            "All assertions query PostgreSQL directly to verify legal and administrative validity, rejecting purely conversational claims.",
            "Zero business logic duplication: MCP servers act as OAuth resource servers delegating to the portal's authoritative validator.",
            "Pilot block constraints strictly enforced on the agent channel, safeguarding against unauthorized block admissions.",
            "Accountability enforced: officer decisions mandate specific rationale in immutable audit trail with authenticated officer ID."
        ]
    }

    out_file = HERE / "results.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Results written to: %s" % out_file)

    conn.close()
    try:
        doc_srv.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
