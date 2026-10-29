# -*- coding: utf-8 -*-
"""Pytest configuration and fixtures for Sewa Setu test suite."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP_DIR = REPO / "app"
MCP_DIR = REPO / "mcp"
EVALS_DIR = REPO / "evals"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(MCP_DIR))
sys.path.insert(0, str(EVALS_DIR))

import app as portal
import oauth as oauth_as
from api import api as api_blueprint


@pytest.fixture
def flask_app():
    """Configured Flask test application."""
    portal.app.config["TESTING"] = True
    portal.app.config["DEBUG"] = False
    return portal.app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def mock_db():
    """Mock database connection and cursor."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with patch("app.get_db", return_value=conn):
        yield conn, cur


@pytest.fixture
def citizen_token():
    """Generate a valid citizen JWT access token."""
    with patch("app.get_db") as mock_db_fn:
        # Provide in-memory RSA key if needed
        token, claims = oauth_as.mint_access_token(
            subject="citizen:9864012345",
            role="citizen",
            scope="citizen",
            audience=portal.config_public_base_url() + "/mcp/citizen",
            client_id="test-client-123",
        )
        return token, claims


@pytest.fixture
def officer_token():
    """Generate a valid officer JWT access token."""
    with patch("app.get_db") as mock_db_fn:
        token, claims = oauth_as.mint_access_token(
            subject="officer:admin",
            role="officer",
            scope="officer",
            audience=portal.config_public_base_url() + "/mcp/officer",
            client_id="test-client-officer",
        )
        return token, claims
