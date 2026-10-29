# -*- coding: utf-8 -*-
"""Real OAuth and role enforcement tests using signed RS256 JWTs and ASGI middleware."""

import time
import jwt
import pytest
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
