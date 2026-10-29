# -*- coding: utf-8 -*-
"""Real OAuth and role enforcement tests using signed RS256 JWTs and ASGI middleware."""

import time
import jwt
import pytest
from unittest.mock import patch, MagicMock
from cryptography.hazmat.primitives.asymmetric import rsa

import app as portal
import oauth as oauth_as
import mcp_common as mc


def test_real_rs256_token_verification():
    """Verify RS256 signed token correctly validates with portal public JWKS."""
    kid, priv = oauth_as.signing_key()
    jwks = oauth_as.public_jwks()

    # Mint valid token
    token, claims = oauth_as.mint_access_token(
        subject="citizen:9864012345",
        role="citizen",
        scope="citizen",
        audience="http://localhost:8000/mcp/citizen",
        client_id="client-test",
    )

    cache = mc.JWKSCache("http://test/jwks.json")
    cache.keys = {k["kid"]: k for k in jwks["keys"]}
    cache.fetched_at = time.time()

    verified = cache.verify(token, "http://localhost:8000/mcp/citizen", oauth_as.ISSUER)
    assert verified["sub"] == "citizen:9864012345"
    assert verified["role"] == "citizen"


def test_reject_expired_jwt():
    kid, priv = oauth_as.signing_key()
    jwks = oauth_as.public_jwks()

    # Mint expired token
    now = int(time.time())
    payload = {
        "iss": oauth_as.ISSUER,
        "sub": "citizen:9864012345",
        "aud": "http://localhost:8000/mcp/citizen",
        "scope": "citizen",
        "role": "citizen",
        "iat": now - 3600,
        "exp": now - 60,
    }
    expired_token = jwt.encode(payload, priv, algorithm="RS256", headers={"kid": kid})

    cache = mc.JWKSCache("http://test/jwks.json")
    cache.keys = {k["kid"]: k for k in jwks["keys"]}
    cache.fetched_at = time.time()

    with pytest.raises(mc.AuthError) as exc_info:
        cache.verify(expired_token, "http://localhost:8000/mcp/citizen", oauth_as.ISSUER)
    assert "token rejected" in exc_info.value.detail


def test_reject_invalid_signature():
    """Token signed by an unauthorized third-party key must be rejected."""
    unauthorized_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid, _ = oauth_as.signing_key()
    jwks = oauth_as.public_jwks()

    now = int(time.time())
    payload = {
        "iss": oauth_as.ISSUER,
        "sub": "citizen:9864012345",
        "aud": "http://localhost:8000/mcp/citizen",
        "scope": "citizen",
        "role": "citizen",
        "iat": now,
        "exp": now + 600,
    }
    tampered_token = jwt.encode(payload, unauthorized_key, algorithm="RS256", headers={"kid": kid})

    cache = mc.JWKSCache("http://test/jwks.json")
    cache.keys = {k["kid"]: k for k in jwks["keys"]}
    cache.fetched_at = time.time()

    with pytest.raises(mc.AuthError) as exc_info:
        cache.verify(tampered_token, "http://localhost:8000/mcp/citizen", oauth_as.ISSUER)
    assert "token rejected" in exc_info.value.detail


def test_reject_wrong_audience():
    """Token minted for citizen MCP must be rejected when sent to officer MCP."""
    kid, priv = oauth_as.signing_key()
    jwks = oauth_as.public_jwks()

    token, _ = oauth_as.mint_access_token(
        subject="citizen:9864012345",
        role="citizen",
        scope="citizen",
        audience="http://localhost:8000/mcp/citizen",
        client_id="client-test",
    )

    cache = mc.JWKSCache("http://test/jwks.json")
    cache.keys = {k["kid"]: k for k in jwks["keys"]}
    cache.fetched_at = time.time()

    with pytest.raises(mc.AuthError) as exc_info:
        cache.verify(token, "http://localhost:8000/mcp/officer", oauth_as.ISSUER)
    assert "token rejected" in exc_info.value.detail


def test_oauth_discovery_metadata(client):
    """Verify OAuth 2.1 authorization server discovery endpoint."""
    res = client.get("/.well-known/oauth-authorization-server")
    assert res.status_code == 200
    data = res.get_json()
    assert data["issuer"] == oauth_as.ISSUER
    assert "authorization_endpoint" in data
    assert "token_endpoint" in data
    assert "jwks_uri" in data
    assert "S256" in data.get("code_challenge_methods_supported", [])
    assert "authorization_code" in data.get("grant_types_supported", [])


def test_oauth_jwks_endpoint(client):
    """Verify JWKS endpoint returns valid RS256 key configuration."""
    res = client.get("/.well-known/jwks.json")
    assert res.status_code == 200
    data = res.get_json()
    assert "keys" in data
    assert len(data["keys"]) > 0
    k = data["keys"][0]
    assert k.get("kty") == "RSA"
    assert k.get("alg") == "RS256"
    assert k.get("use") == "sig"
    assert "n" in k and "e" in k and "kid" in k


def test_oauth_dynamic_client_registration(client):
    """Test dynamic client registration validation (RFC 7591)."""
    # 1. Invalid redirect URIs (non-https and non-localhost)
    res = client.post("/oauth/register", json={
        "client_name": "Malicious App",
        "redirect_uris": ["http://attacker.com/oauth/callback"],
    })
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid_redirect_uri"

    # 2. Valid localhost native client redirect URI
    with patch("app.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = None

        res = client.post("/oauth/register", json={
            "client_name": "Trusted Local Agent",
            "redirect_uris": ["http://localhost:8080/callback"],
        })
        assert res.status_code == 201
        data = res.get_json()
        assert "client_id" in data
        assert data.get("token_endpoint_auth_method") == "none"
        assert data["client_name"] == "Trusted Local Agent"


def test_oauth_redirect_uri_matching():
    """Verify loopback ephemeral port matching rule (RFC 8252 s7.3)."""
    registered = ["http://localhost/callback", "http://127.0.0.1/callback", "https://example.gov.in/auth/callback"]

    # Exact match
    assert oauth_as.redirect_allowed(registered, "https://example.gov.in/auth/callback")

    # Loopback with ephemeral port matches registered localhost / 127.0.0.1
    assert oauth_as.redirect_allowed(registered, "http://localhost:12345/callback")
    assert oauth_as.redirect_allowed(registered, "http://127.0.0.1:9090/callback")

    # Mismatched paths or non-loopback schemes must be rejected
    assert not oauth_as.redirect_allowed(registered, "https://example.gov.in/other")
    assert not oauth_as.redirect_allowed(registered, "http://attacker.com:8080/callback")


def test_reject_refresh_token_as_access_token(client):
    """Ensure refresh tokens cannot be presented as access tokens to call protected APIs."""
    # Mint token with kind='refresh'
    refresh_token, _ = oauth_as.mint_access_token(
        subject="citizen:9864012345",
        role="citizen",
        scope="citizen",
        audience=portal.config_public_base_url() + "/mcp/citizen",
        client_id="client-test",
        kind="refresh",
    )

    res = client.get("/api/citizen/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert res.status_code == 401
    assert res.get_json()["detail"] == "token must be an access token"

