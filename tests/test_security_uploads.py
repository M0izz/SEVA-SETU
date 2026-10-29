# -*- coding: utf-8 -*-
"""Comprehensive tests for document upload security, magic byte validation, and storage safety."""

import os
import base64
import pytest
from unittest.mock import patch, MagicMock

import app as portal
import api as api_module


def test_upload_valid_pdf_magic_bytes(client, citizen_token):
    token, _ = citizen_token
    # Valid PDF magic bytes
    pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\ntrailer<</Root 1 0 R>>\nstartxref\n9\n%%EOF"
    b64 = base64.b64encode(pdf_content).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "proof.pdf"}, "dry_run": True})
    assert res.status_code == 200
    assert res.get_json()["dry_run"] is True


def test_upload_valid_jpeg_magic_bytes(client, citizen_token):
    token, _ = citizen_token
    # Valid JPEG magic bytes (\xff\xd8\xff)
    jpeg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb"
    b64 = base64.b64encode(jpeg_content).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "photo.jpg"}, "dry_run": True})
    assert res.status_code == 200
    assert res.get_json()["dry_run"] is True


def test_upload_valid_png_magic_bytes(client, citizen_token):
    token, _ = citizen_token
    # Valid PNG magic bytes (\x89PNG\r\n\x1a\n)
    png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b64 = base64.b64encode(png_content).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "photo.png"}, "dry_run": True})
    assert res.status_code == 200
    assert res.get_json()["dry_run"] is True


def test_upload_rejects_html_polyglot(client, citizen_token):
    token, _ = citizen_token
    # HTML script payload disguised with .pdf filename
    html_content = b"<!DOCTYPE html><html><body><script>alert('xss')</script></body></html>"
    b64 = base64.b64encode(html_content).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "proof.pdf"}, "dry_run": True})
    assert res.status_code == 400
    assert "not permitted" in res.get_json()["detail"]


def test_upload_rejects_svg_content(client, citizen_token):
    token, _ = citizen_token
    svg_content = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    b64 = base64.b64encode(svg_content).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "image.png"}, "dry_run": True})
    assert res.status_code == 400
    assert "not permitted" in res.get_json()["detail"]


def test_upload_rejects_executable_binaries(client, citizen_token):
    token, _ = citizen_token
    # Windows MZ PE header
    dos_exec = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    b64 = base64.b64encode(dos_exec).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "proof.pdf"}, "dry_run": True})
    assert res.status_code == 400

    # Linux ELF header
    elf_exec = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b64_elf = base64.b64encode(elf_exec).decode()
    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64_elf, "filename": "proof.pdf"}, "dry_run": True})
    assert res.status_code == 400


def test_upload_rejects_corrupted_or_arbitrary_text(client, citizen_token):
    token, _ = citizen_token
    text_content = b"This is just a plain text note, not a valid PDF or JPG document."
    b64 = base64.b64encode(text_content).decode()

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": b64, "filename": "proof.pdf"}, "dry_run": True})
    assert res.status_code == 400
    assert "unrecognized signature" in res.get_json()["detail"]


def test_upload_rejects_oversized_base64_before_decode(client, citizen_token):
    token, _ = citizen_token
    # Huge base64 string exceeding MAX_B64_LEN
    huge_b64 = "A" * (api_module.MAX_B64_LEN + 100)

    res = client.post("/api/citizen/apply",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"age_proof": {"content_b64": huge_b64, "filename": "proof.pdf"}, "dry_run": True})
    assert res.status_code == 400
    assert "larger than 5 MB" in res.get_json()["detail"]
