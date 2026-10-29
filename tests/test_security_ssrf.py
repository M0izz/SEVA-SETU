# -*- coding: utf-8 -*-
"""Comprehensive automated tests for SSRF prevention in age-proof ingestion."""

import pytest
from unittest.mock import patch, MagicMock
import app as portal
import api as api_module


def test_ssrf_rejects_loopback(client, citizen_token):
    token, _ = citizen_token
    targets = [
        "http://127.0.0.1/proof.pdf",
        "http://127.0.0.1:8000/proof.pdf",
        "http://localhost/proof.pdf",
        "http://localhost:8000/proof.pdf",
    ]
    for url in targets:
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": url}, "dry_run": True})
        assert res.status_code == 400
        assert res.get_json()["error"] == "bad_age_proof"
        assert any(term in res.get_json()["detail"].lower() for term in ["prohibited", "internal", "loopback", "private"])


def test_ssrf_rejects_private_ips(client, citizen_token):
    token, _ = citizen_token
    private_urls = [
        "http://10.0.0.1/doc.pdf",
        "http://10.255.255.254/doc.pdf",
        "http://172.16.0.1/doc.pdf",
        "http://172.20.0.5/doc.pdf",
        "http://192.168.1.1/doc.pdf",
        "http://192.168.0.100/doc.pdf",
    ]
    for url in private_urls:
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": url}, "dry_run": True})
        assert res.status_code == 400
        assert res.get_json()["error"] == "bad_age_proof"


def test_ssrf_rejects_cloud_metadata(client, citizen_token):
    token, _ = citizen_token
    metadata_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
    ]
    for url in metadata_urls:
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": url}, "dry_run": True})
        assert res.status_code == 400
        assert res.get_json()["error"] == "bad_age_proof"


def test_ssrf_rejects_internal_docker_services(client, citizen_token):
    token, _ = citizen_token
    docker_urls = [
        "http://smsgw:8025/api/messages",
        "http://db:5432/",
        "http://app:8000/api/citizen/me",
        "http://scheduler:8000/",
        "http://caddy:80/",
    ]
    for url in docker_urls:
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": url}, "dry_run": True})
        assert res.status_code == 400
        assert res.get_json()["error"] == "bad_age_proof"


def test_ssrf_rejects_ipv6_internal(client, citizen_token):
    token, _ = citizen_token
    ipv6_urls = [
        "http://[::1]/doc.pdf",
        "http://[::ffff:127.0.0.1]/doc.pdf",
        "http://[fe80::1]/doc.pdf",
    ]
    for url in ipv6_urls:
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": url}, "dry_run": True})
        assert res.status_code == 400
        assert res.get_json()["error"] == "bad_age_proof"


def test_ssrf_rejects_redirects(client, citizen_token):
    """Even if an initial URL looks benign, HTTP redirects must be rejected."""
    token, _ = citizen_token
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": "http://127.0.0.1:8000/admin"}

    with patch("socket.getaddrinfo") as mock_dns, patch("requests.get", return_value=mock_resp):
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 80))] # Public IP
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": "http://example.com/redirect.pdf"}, "dry_run": True})
        assert res.status_code == 400
        assert "redirects are not permitted" in res.get_json()["detail"]


def test_ssrf_rejects_malformed_and_unsupported_schemes(client, citizen_token):
    token, _ = citizen_token
    bad_urls = [
        "ftp://example.com/doc.pdf",
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://admin:secret@example.com/doc.pdf", # embedded credentials
        "not-a-valid-url",
        "http:///empty-host",
    ]
    for url in bad_urls:
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": url}, "dry_run": True})
        assert res.status_code == 400
        assert res.get_json()["error"] == "bad_age_proof"


def test_ssrf_rejects_oversized_response(client, citizen_token):
    token, _ = citizen_token
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Stream chunks totaling over 5MB
    mock_resp.iter_content.return_value = [b"X" * (1024 * 1024) for _ in range(6)]

    with patch("socket.getaddrinfo") as mock_dns, patch("requests.get", return_value=mock_resp):
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 80))]
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": "http://example.com/large.pdf"}, "dry_run": True})
        assert res.status_code == 400
        assert "larger than 5 MB" in res.get_json()["detail"]
