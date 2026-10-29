# -*- coding: utf-8 -*-
"""Shared machinery for the two Sewa Setu MCP servers.

Each server is an OAuth 2.1 *resource server* (RFC 9728) in front of the
portal's JSON API:

  - tokens are RS256 JWTs minted by the portal's authorization server;
    we verify iss / aud / role / scope with the portal's public JWKS.
  - aud must equal this server's canonical URL (PORTAL_PUBLIC_BASE + /mcp/<role>),
    so a token minted for the citizen MCP cannot be replayed at the officer MCP.
  - tools never touch the database directly: they call the portal's JSON API,
    which re-verifies the same token (defence in depth) and reuses the portal's
    own validation functions. The portal remains the system of record.
"""

import os
import time
import contextvars

import httpx
import jwt
from starlette.responses import JSONResponse

PORTAL_URL = os.environ.get("PORTAL_URL", "http://localhost:8000").rstrip("/")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", PORTAL_URL).rstrip("/")

# claims + raw token of the current MCP request (set by the auth middleware)
_current_auth = contextvars.ContextVar("current_auth", default=None)


class AuthError(Exception):
    def __init__(self, code, detail, status=401):
        self.code = code
        self.detail = detail
        self.status = status


class JWKSCache:
    """RS256 public keys of the portal, refreshed on unknown kid."""

    def __init__(self, url):
        self.url = url
        self.keys = {}
        self.fetched_at = 0.0

    def _fetch(self):
        r = httpx.get(self.url, timeout=10)
        r.raise_for_status()
        self.keys = {}
        for k in r.json().get("keys", []):
            if k.get("kty") == "RSA" and k.get("use", "sig") == "sig":
                self.keys[k.get("kid")] = k
        self.fetched_at = time.time()

    def verify(self, token, audience, issuer):
        def key_for(header_kid):
            if time.time() - self.fetched_at > 300 or header_kid not in self.keys:
                self._fetch()
            jwk = self.keys.get(header_kid)
            if not jwk:
                raise AuthError("invalid_token", "signing key not found in portal JWKS")
            return jwt.algorithms.RSAAlgorithm.from_jwk(jwk)

        try:
            header = jwt.get_unverified_header(token)
            key = key_for(header.get("kid"))
            return jwt.decode(token, key, algorithms=["RS256"],
                              audience=audience, issuer=issuer)
        except jwt.PyJWTError as e:
            raise AuthError("invalid_token", "token rejected: %s" % e)
        except httpx.HTTPError as e:
            raise AuthError("temporarily_unavailable",
                            "could not reach portal JWKS: %s" % e, 503)


def canonical_url(role):
    return PUBLIC_BASE + "/mcp/" + role


def protected_resource_doc(role):
    return {
        "resource": canonical_url(role),
        "authorization_servers": [PUBLIC_BASE],
        "scopes_supported": [role],
        "bearer_methods_supported": ["header"],
        "resource_documentation": PUBLIC_BASE + "/about",
    }


def auth_error_response(err: AuthError, role):
    resp = JSONResponse({"error": err.code, "detail": err.detail}, status_code=err.status)
    if err.status == 401:
        resp.headers["WWW-Authenticate"] = (
            'Bearer error="%s", error_description="%s", '
            'resource_metadata="%s/.well-known/oauth-protected-resource/mcp/%s"'
            % (err.code, err.detail.replace('"', "'"), PUBLIC_BASE, role))
    return resp


class RequireBearerMiddleware:
    """Pure-ASGI middleware: every /mcp/<role> request must carry a valid
    Bearer token issued by the portal for THIS server's canonical URL."""

    def __init__(self, app, role):
        self.app = app
        self.role = role
        self.jwks = JWKSCache(PORTAL_URL + "/.well-known/jwks.json")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp/"):
            await self.app(scope, receive, send)
            return
        headers = dict((k.decode("latin-1").lower(), v.decode("latin-1"))
                       for k, v in scope.get("headers", []))
        auth = headers.get("authorization", "")
        if scope.get("method") == "OPTIONS":   # preflight carries no token
            await self.app(scope, receive, send)
            return
        if not auth.startswith("Bearer "):
            await auth_error_response(
                AuthError("missing_token",
                          "sign in at the Sewa Setu portal; this MCP server accepts only "
                          "its OAuth access tokens"), self.role)(scope, receive, send)
            return
        token = auth[7:].strip()
        try:
            claims = self.jwks.verify(token, canonical_url(self.role), PUBLIC_BASE)
        except AuthError as e:
            await auth_error_response(e, self.role)(scope, receive, send)
            return
        if claims.get("role") != self.role:
            await auth_error_response(
                AuthError("insufficient_scope",
                          "this server's tools need the %r role; your token has %r"
                          % (self.role, claims.get("role")), 403),
                self.role)(scope, receive, send)
            return
        _current_auth.set({"token": token, "claims": claims})
        await self.app(scope, receive, send)


def current_auth():
    a = _current_auth.get()
    if not a:
        raise AuthError("missing_token", "no authenticated request context")
    return a


async def call_portal(method, path, json_body=None, params=None):
    """Authenticated server-to-server call into the portal's JSON API.
    The user's token is forwarded; the portal re-verifies it."""
    a = current_auth()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, PORTAL_URL + path,
                                 json=json_body, params=params,
                                 headers={"Authorization": "Bearer " + a["token"],
                                          "Accept": "application/json"})
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text}
