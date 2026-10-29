# -*- coding: utf-8 -*-
"""Real integration tests for MCP server startup, tool registry separation, and client invocation."""

import asyncio
import os
import pytest
import uvicorn
import httpx
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import mcp_citizen
import mcp_officer
import mcp_common as mc
import app as portal
import oauth as oauth_as


def test_server_registries_separation():
    """Verify tool registries: citizen and officer tools must be strictly separated."""
    # List citizen tools
    citizen_tools = [t.name for t in mcp_citizen.mcp._tool_manager.list_tools()]
    officer_tools = [t.name for t in mcp_officer.mcp._tool_manager.list_tools()]

    expected_citizen = {
        "get_scheme_info",
        "check_eligibility",
        "get_required_documents",
        "apply_for_pension",
        "get_my_application",
        "withdraw_my_application",
    }
    expected_officer = {
        "queue_summary",
        "list_applications",
        "get_application",
        "decide_application",
    }

    assert set(citizen_tools) == expected_citizen, f"Citizen tools mismatch: {citizen_tools}"
    assert set(officer_tools) == expected_officer, f"Officer tools mismatch: {officer_tools}"

    # Strict separation: zero overlap
    overlap = set(citizen_tools).intersection(set(officer_tools))
    assert len(overlap) == 0, f"Unauthorized tool overlap detected: {overlap}"


def test_build_apps():
    """Verify both ASGI apps build cleanly without import or configuration errors."""
    app_citizen = mcp_citizen.build_app()
    app_officer = mcp_officer.build_app()

    assert app_citizen is not None
    assert app_officer is not None
    assert isinstance(app_citizen, mc.RequireBearerMiddleware)
    assert isinstance(app_officer, mc.RequireBearerMiddleware)


@pytest.mark.asyncio
async def test_real_citizen_mcp_server_flow():
    """Start real citizen MCP server, connect with real MCP client over Streamable HTTP,
    and verify tool discovery and invocation."""
    app = mcp_citizen.build_app()
    port = 8991
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    await asyncio.sleep(0.4)

    # 1. Unauthenticated request should be rejected by middleware
    async with httpx.AsyncClient() as client:
        res = await client.post(f"http://127.0.0.1:{port}/mcp/citizen", json={})
        assert res.status_code == 401

    # 2. Authenticated request with real signed citizen token
    # Mock JWKS cache in middleware so it validates our test token
    test_kid, test_priv = oauth_as.signing_key()
    test_token, _ = oauth_as.mint_access_token(
        subject="citizen:9864012345",
        role="citizen",
        scope="citizen",
        audience=portal.config_public_base_url() + "/mcp/citizen",
        client_id="test-client-1",
    )

    with patch.object(app.jwks, "verify", return_value={"sub": "citizen:9864012345", "role": "citizen"}):
        headers = {"Authorization": f"Bearer {test_token}"}
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp/citizen", http_client=client) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    discovered_names = [t.name for t in tools.tools]
                    assert "get_required_documents" in discovered_names
                    assert "decide_application" not in discovered_names

                    # Invoke authorized get_required_documents tool
                    result = await session.call_tool("get_required_documents", {})
                    assert not getattr(result, "is_error", getattr(result, "isError", False))
                    assert any("Age Proof Document" in c.text for c in result.content if hasattr(c, "text"))

    srv.should_exit = True
    await task


@pytest.mark.asyncio
async def test_real_officer_mcp_server_flow():
    """Start real officer MCP server and verify tool discovery with officer token."""
    app = mcp_officer.build_app()
    port = 8992
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    await asyncio.sleep(0.4)

    # 1. Unauthenticated request must be 401
    async with httpx.AsyncClient() as client:
        res = await client.post(f"http://127.0.0.1:{port}/mcp/officer", json={})
        assert res.status_code == 401

    # 2. Citizen token used on officer server must be rejected with 403 insufficient_scope
    citizen_token, _ = oauth_as.mint_access_token(
        subject="citizen:9864012345",
        role="citizen",
        scope="citizen",
        audience=portal.config_public_base_url() + "/mcp/citizen",
        client_id="test-client-1",
    )

    with patch.object(app.jwks, "verify", return_value={"sub": "citizen:9864012345", "role": "citizen"}):
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {citizen_token}"}) as client:
            res = await client.post(f"http://127.0.0.1:{port}/mcp/officer", json={})
            assert res.status_code == 403


    # Valid officer token
    officer_token, _ = oauth_as.mint_access_token(
        subject="officer:admin",
        role="officer",
        scope="officer",
        audience=portal.config_public_base_url() + "/mcp/officer",
        client_id="test-officer-client",
    )

    with patch.object(app.jwks, "verify", return_value={"sub": "officer:admin", "role": "officer"}):
        headers = {"Authorization": f"Bearer {officer_token}"}
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp/officer", http_client=client) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    discovered_names = [t.name for t in tools.tools]
                    assert "queue_summary" in discovered_names
                    assert "apply_for_pension" not in discovered_names

    srv.should_exit = True
    await task
