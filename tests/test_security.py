"""Host validation, security headers and the CSP (Backend V2, Group A)."""
import base64
import hashlib
import re
from pathlib import Path

import pytest

from autopatch import create_app
from autopatch.core.security import build_csp, host_allowed, inline_script_hashes, split_host

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["/", "/app", "/app/repositories", "/app/scan", "/app/issues", "/app/studio", "/app/verification", "/app/reports"]


@pytest.fixture()
def app(tmp_path):
    return create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local"})


@pytest.mark.parametrize("host", ["localhost", "localhost:5000", "127.0.0.1", "127.0.0.1:8080", "[::1]", "[::1]:5000", "LOCALHOST:5000",
                                  "app.localhost:3000", "localhost."])
def test_local_hosts_are_accepted(host):
    assert host_allowed(host)


@pytest.mark.parametrize("host", ["", "evil.com", "evil.com:5000", "localhost.evil.com", "127.0.0.1.evil.com", "192.168.1.5", "0.0.0.0",
                                  "localhost:abc", "localhost:99999999", "[::1", "[evil]", "local host", "evil.com#@localhost", "a" * 300,
                                  "localhost@evil.com", "evil.com/localhost"])
def test_other_hosts_are_rejected(host):
    assert not host_allowed(host)


def test_allowed_hosts_extension():
    assert not host_allowed("dev.box")
    assert host_allowed("dev.box:5000", ["dev.box"]) and not host_allowed("evil.com", ["dev.box"])


def test_split_host_handles_ports_and_ipv6():
    assert split_host("[::1]:5000") == "::1" and split_host("Localhost:80") == "localhost" and split_host("x:y") is None


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "localhost:5000", "127.0.0.1:5000", "[::1]:5000"])
def test_every_page_and_asset_works_on_each_local_name(app, host):
    c = app.test_client()
    for path in PAGES + ["/static/css/ap.css", "/static/js/shell.js", "/api/ai/status", "/api/projects"]:
        r = c.get(path, headers={"Host": host})
        assert r.status_code == 200, (host, path, r.status_code)


@pytest.mark.parametrize("path", PAGES + ["/static/css/ap.css", "/static/js/common.js", "/api/projects", "/api/ai/status", "/api/overview", "/nope"])
def test_foreign_host_is_rejected_everywhere_including_get(app, path):
    r = app.test_client().get(path, headers={"Host": "attacker.example:5000"})
    assert r.status_code == 403 and "localhost" in r.get_data(as_text=True)


def test_foreign_host_cannot_post_either(app):
    r = app.test_client().post("/api/projects", headers={"Host": "attacker.example"}, data={})
    assert r.status_code == 403


def test_allowed_hosts_setting_is_honoured(tmp_path):
    a = create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "ALLOWED_HOSTS": ["dev.box"]})
    assert a.test_client().get("/api/ai/status", headers={"Host": "dev.box:5000"}).status_code == 200
    assert a.test_client().get("/api/ai/status", headers={"Host": "other.box"}).status_code == 403


def test_security_headers_on_pages_api_static_and_errors(app):
    c = app.test_client()
    for path in PAGES + ["/api/ai/status", "/static/css/ap.css", "/missing-page", "/api/missing"]:
        h = c.get(path).headers
        assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY" and h["Referrer-Policy"] == "no-referrer", path
        assert "default-src 'self'" in h["Content-Security-Policy"] and "frame-ancestors 'none'" in h["Content-Security-Policy"], path


def test_csp_is_strict_for_scripts():
    csp = build_csp(ROOT / "templates")
    script = next(p for p in csp.split("; ") if p.startswith("script-src"))
    assert "unsafe-inline" not in script and "unsafe-eval" not in script and "'self'" in script
    assert "object-src 'none'" in csp and "connect-src 'self'" in csp and "base-uri 'self'" in csp


def test_csp_hashes_match_every_inline_script_that_is_rendered(app):
    """Render each page, find real inline scripts in the output and check each one is allowed by hash."""
    c = app.test_client()
    for path in PAGES:
        r = c.get(path)
        csp = r.headers["Content-Security-Policy"]
        for _attrs, body in re.findall(r"<script(?![^>]*\bsrc\s*=)([^>]*)>(.*?)</script>", r.get_data(as_text=True), re.S | re.I):
            token = "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
            assert token in csp, (path, body[:60])


def test_templates_have_no_inline_event_handlers_or_javascript_urls():
    for p in (ROOT / "templates").glob("*.html"):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=", text, re.I), p.name
        assert "javascript:" not in text.lower(), p.name


def test_static_files_do_not_load_external_resources():
    for p in list((ROOT / "static").rglob("*.css")) + list((ROOT / "static").rglob("*.js")) + list((ROOT / "templates").glob("*.html")):
        for url in re.findall(r"""(?:src|href|url\(|@import)\s*=?\s*['"(]?\s*(https?:)?//([^'")\s]+)""", p.read_text(encoding="utf-8")):
            assert url[1].startswith("www.w3.org"), (p.name, url)


def test_inline_script_hashes_lists_the_landing_script():
    assert len(inline_script_hashes(ROOT / "templates")) == 1
