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

    with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get", return_value=mock_resp):
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

    with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get", return_value=mock_resp):
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 80))]
        res = client.post("/api/citizen/apply",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"age_proof": {"url": "http://example.com/large.pdf"}, "dry_run": True})
        assert res.status_code == 400
        assert "larger than 5 MB" in res.get_json()["detail"]


def test_ssrf_dns_rebinding_pins_transport():
    """Prove that PinnedIPAdapter connects to the validated IP without re-resolving hostname,
    preserving the Host header and defeating DNS rebinding attacks."""
    import http.server
    import threading

    captured = {}

    class TestHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            captured["host_header"] = self.headers.get("Host")
            captured["path"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), TestHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    # The domain name is completely unresolvable in DNS
    unresolvable_url = f"http://rebinding-attacker-fake-domain.test:{server_port}/doc.pdf"

    # In eval/test mode, ALLOWED_DOC_HOSTS allows our test server port
    with patch.dict("os.environ", {"ALLOWED_DOC_HOSTS": f"127.0.0.1:{server_port},rebinding-attacker-fake-domain.test:{server_port}", "ALLOW_TEST_DOC_SERVERS": "1"}):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", server_port))]):
            content, err = api_module._validate_and_fetch_url(unresolvable_url)

    server.server_close()
    thread.join(timeout=2)

    assert err is None, f"Expected successful fetch, got: {err}"
    assert content.startswith(b"%PDF-")
    assert captured.get("host_header") == f"rebinding-attacker-fake-domain.test:{server_port}"


def test_ssrf_rejects_hostname_resolving_to_private_ip():
    """A public domain name that resolves to a private IP must be rejected immediately."""
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.50", 80))]):
        content, err = api_module._validate_and_fetch_url("http://attacker-controlled-rebinding.com/proof.pdf")
        assert content is None
        assert "private, loopback, link-local, or internal IP" in err


def test_ssrf_rejects_hostname_rebinding_to_metadata_ip():
    """A hostname resolving to cloud metadata IP 169.254.169.254 must be blocked."""
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 80))]):
        content, err = api_module._validate_and_fetch_url("http://cloud-meta.attacker.com/proof.pdf")
        assert content is None
        assert "private, loopback, link-local, or internal IP" in err


def test_ssrf_rejects_mixed_public_and_private_dns_answers():
    """If a hostname returns multiple IP addresses where one is public and one is private,
    the request must be rejected entirely to prevent multihomed rebinding."""
    mixed_answers = [
        (2, 1, 6, "", ("93.184.216.34", 80)),  # Public
        (2, 1, 6, "", ("10.0.0.5", 80)),        # Private RFC 1918
    ]
    with patch("socket.getaddrinfo", return_value=mixed_answers):
        content, err = api_module._validate_and_fetch_url("http://mixed-dns.attacker.com/proof.pdf")
        assert content is None
        assert "private, loopback, link-local, or internal IP" in err


def test_ssrf_rejects_cgnat_and_documentation_ips():
    """Non-global IPs such as CGNAT (100.64.0.0/10) and documentation IPs must be rejected."""
    prohibited_ips = ["100.64.0.1", "192.0.2.1", "198.51.100.1", "203.0.113.1", "198.18.0.1"]
    for ip in prohibited_ips:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 80))]):
            content, err = api_module._validate_and_fetch_url(f"http://test-{ip.replace('.', '-')}.example.com/doc.pdf")
            assert content is None
            assert "private, loopback, link-local, or internal IP" in err


def test_ssrf_rejects_unusual_ip_encodings():
    """Unusual IP encodings (octal, hex, dword, dotted-short) must be canonicalized and rejected."""
    unusual_urls = [
        "http://0177.0.0.1/doc.pdf",     # Octal loopback
        "http://2130706433/doc.pdf",     # Dword loopback
        "http://0x7f000001/doc.pdf",     # Hex loopback
        "http://127.1/doc.pdf",          # Short-dotted loopback
    ]
    for url in unusual_urls:
        content, err = api_module._validate_and_fetch_url(url)
        assert content is None
        assert "internal" in err or "private" in err


def test_ssrf_rejects_trailing_dot_internal_hosts():
    """Hostnames with trailing dots like 'localhost.' must still be caught as internal."""
    for url in ["http://localhost./doc.pdf", "http://127.0.0.1./doc.pdf"]:
        content, err = api_module._validate_and_fetch_url(url)
        assert content is None
        assert "internal" in err or "private" in err or "could not be resolved" in err


def test_ssrf_rejects_oversized_content_length_header():
    """A response advertising Content-Length > 5MB must be aborted before streaming body."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Length": "10485760"}  # 10 MB
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 80))]):
        with patch("requests.Session.get", return_value=mock_resp):
            content, err = api_module._validate_and_fetch_url("http://example.com/giant.pdf")
            assert content is None
            assert "file is larger than 5 MB" in err
            mock_resp.close.assert_called()


def test_ssrf_allowlist_in_production_cannot_authorize_private_destinations():
    """In production mode, an allowlisted hostname that resolves to a private IP must STILL be rejected."""
    # os.environ["ALLOWED_DOC_HOSTS"] set, but ALLOW_TEST_DOC_SERVERS is NOT set
    with patch.dict("os.environ", {"ALLOWED_DOC_HOSTS": "trusted-portal.gov.in"}, clear=False):
        # Even if allowlisted, DNS resolves to private IP
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.99", 80))]):
            content, err = api_module._validate_and_fetch_url("http://trusted-portal.gov.in/doc.pdf")
            assert content is None
            assert "private, loopback, link-local, or internal IP" in err


