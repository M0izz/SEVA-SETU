# -*- coding: utf-8 -*-
"""OAuth + MCP client helpers for the Sewa Setu evaluation harness.

This is a REAL MCP client (the official `mcp` SDK) driven end-to-end:
  1. dynamic client registration on the portal (RFC 7591)
  2. authorization-code + PKCE sign-in exactly as a citizen or officer does it
     (citizens: mobile + OTP read from the simulated SMS gateway; officers:
     departmental login) with the MCP server URL as the OAuth `resource`
  3. connection to the MCP server over streamable HTTP with the Bearer token
"""

import base64
import hashlib
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REDIRECT_URI = "http://localhost:17777/callback"


class MCPConnectError(Exception):
    """Raised when the MCP server refuses the session (bad/absent token)."""


def _pkce():
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def fetch_otp(gateway_url, mobile, timeout=25, min_id=0):
    """Read the citizen's OTP from the simulated SMS gateway (1-4s latency).
    Only accepts messages with id > min_id — so a previous sign-in's OTP for
    the same number can never satisfy this sign-in."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(gateway_url + "/api/messages", params={"to": mobile}, timeout=5)
            msgs = [m for m in r.json().get("messages", [])
                    if m.get("delivered") and m.get("text")
                    and m.get("id", 0) > min_id
                    and re.search(r"OTP is (\d{6})", m["text"])]
            if msgs:
                newest = max(msgs, key=lambda m: m["id"])
                return re.search(r"OTP is (\d{6})", newest["text"]).group(1)
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("no OTP delivered for " + mobile)


def _gateway_baseline(gateway, mobile):
    try:
        r = httpx.get(gateway + "/api/messages", params={"to": mobile}, timeout=5)
        msgs = r.json().get("messages", [])
        return max((m["id"] for m in msgs), default=0)
    except Exception:
        return 0


def _extract_code(location):
    q = parse_qs(urlparse(location).query)
    return q["code"][0]


def get_token(portal, role, resource, username=None, password=None,
              mobile=None, gateway=None, debug=False):
    """Full browser-equivalent OAuth sign-in. Returns the token response dict."""
    def note(step, r):
        if debug:
            print("   [%s] %s -> %s %s" % (step, r.request.method + " " +
                                           str(r.request.url)[:90],
                                           r.status_code,
                                           r.headers.get("Location", "")[:70]))
    with httpx.Client(follow_redirects=False, timeout=15) as c:
        # 1. dynamic client registration
        r = c.post(portal + "/oauth/register", json={
            "client_name": "sewasetu-eval-harness",
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none"})
        r.raise_for_status()
        client_id = r.json()["client_id"]

        # 2. authorization request (PKCE S256, resource indicator)
        verifier, challenge = _pkce()
        r = c.get(portal + "/oauth/authorize", params={
            "client_id": client_id, "redirect_uri": REDIRECT_URI,
            "response_type": "code", "scope": role, "resource": resource,
            "state": "eval-" + secrets.token_hex(4),
            "code_challenge": challenge, "code_challenge_method": "S256"})
        loc = r.headers.get("Location", "")
        assert r.status_code in (302, 303), "authorize failed: %s" % r.status_code

        # 3. portal sign-in
        if role == "citizen":
            baseline = _gateway_baseline(gateway, mobile)
            login_path = urlparse(loc).path + "?" + urlparse(loc).query
            r = c.get(portal + login_path)                  # load the login page
            note("login-page", r)
            r = c.post(portal + login_path, data={"mobile": mobile})
            note("post-mobile", r)
            assert b"OTP" in r.content or r.status_code == 200, "mobile step failed"
            otp = fetch_otp(gateway, mobile, min_id=baseline)
            if debug:
                print("   [otp] fetched:", otp)
            r = c.post(portal + login_path, data={"otp": otp})
            note("post-otp", r)
        else:
            login_path = urlparse(loc).path + "?" + urlparse(loc).query
            r = c.post(portal + login_path, data={"username": username,
                                                  "password": password})
            note("post-officer", r)
        loc = r.headers.get("Location", "")
        assert "consent" in loc, "sign-in failed: %s" % loc[:200]

        # 4. consent
        consent_path = urlparse(loc).path + "?" + urlparse(loc).query
        r = c.post(portal + consent_path, data={"decision": "allow"})
        loc = r.headers.get("Location", "")
        assert "code=" in loc, "consent failed: %s" % loc[:200]
        code = _extract_code(loc)

        # 5. token exchange
        r = c.post(portal + "/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": client_id, "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier, "resource": resource})
        assert r.status_code == 200, "token exchange failed: %s %s" % (
            r.status_code, r.text[:200])
        return r.json()


class MCPSession:
    """A real MCP client session over streamable HTTP with the Bearer token."""

    def __init__(self, url, token):
        self.url = url
        self.token = token
        self._client = None

    async def __aenter__(self):
        headers = {"Authorization": "Bearer " + self.token}
        self._client = httpx.AsyncClient(headers=headers, timeout=60,
                                         follow_redirects=True)
        self._stack = streamable_http_client(self.url, http_client=self._client)
        try:
            read, write, _ = await self._stack.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
        except BaseException as e:
            try:
                await self.__aexit__(type(e), e, None)
            except BaseException:
                pass
            raise MCPConnectError(
                "MCP session rejected at %s (%s: %s)" % (self.url,
                                                         type(e).__name__, e)) from e
        return self

    async def __aexit__(self, *exc):
        try:
            if getattr(self, "_session", None):
                await self._session.__aexit__(*exc)
            if getattr(self, "_stack", None):
                await self._stack.__aexit__(*exc)
        except Exception:
            pass
        if self._client:
            await self._client.aclose()

    async def list_tools(self):
        return await self._session.list_tools()

    async def call(self, name, arguments=None):
        res = await self._session.call_tool(name, arguments or {})
        texts = []
        for item in res.content:
            if getattr(item, "text", None):
                texts.append(item.text)
        return {"is_error": res.isError, "text": "\n".join(texts)}
