"""Front-end pages: every route renders, old URLs still work, Patchy is preserved, nothing inserts untrusted HTML."""
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCHY_SHA256 = "9f6bb39607716bcca8f78432fabee85dd17911e2b127b19bbb1d5f070f9fcbc1"  # the Patchy <symbol> artwork exactly as in the verified baseline

PAGES = ["/", "/app", "/app/repositories", "/app/scan", "/app/issues", "/app/studio", "/app/verification", "/app/reports"]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    r = client.get(path)
    assert r.status_code == 200 and b"AutoPatch" in r.data


@pytest.mark.parametrize("path", ["/app/repositories/abc", "/app/findings/abc", "/app/studio/abc"])
def test_detail_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_old_addresses_redirect(client):
    assert client.get("/analyze").headers["Location"].endswith("/app/repositories")
    assert client.get("/app/analyze").headers["Location"].endswith("/app/repositories")
    assert client.get("/app/projects/abc").headers["Location"].endswith("/app/repositories/abc")


def test_workspace_has_all_eight_destinations_and_no_placeholders(client):
    html = client.get("/app").get_data(as_text=True)
    for href in ["/app\"", "/app/repositories", "/app/scan", "/app/issues", "/app/studio", "/app/verification", "/app/reports"]:
        assert 'href="' + href.rstrip('"') in html
    assert "coming later" not in html and "Stage 2" not in html


def test_landing_copy_and_patchy(client):
    html = client.get("/").get_data(as_text=True)
    assert "Detect." in html and "Repair." in html and "Verify." in html and "Get Started" in html and "Explore Features" in html
    m = re.search(r'<symbol id="patchy".*?</symbol>', html, re.S)
    assert m and hashlib.sha256(m.group(0).encode()).hexdigest() == PATCHY_SHA256
    assert 'id="hero-bot"' in html and 'id="scene"' in html and 'id="steps"' in html


def test_static_assets_referenced_by_templates_exist(client):
    seen = set()
    for page in PAGES + ["/app/repositories/x", "/app/findings/x", "/app/studio/x"]:
        seen |= set(re.findall(r'(?:src|href)="(/static/[^"?]+)', client.get(page).get_data(as_text=True)))
    assert len(seen) >= 8
    for url in seen:
        assert client.get(url).status_code == 200, url


def test_scripts_never_use_innerhtml_or_eval():
    for f in (ROOT / "static" / "js").glob("*.js"):
        text = "\n".join(ln for ln in f.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("//"))  # comments may mention the words
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function", text), f.name


def test_no_external_resources_in_templates_or_css():
    for f in list((ROOT / "templates").glob("*.html")) + list((ROOT / "static" / "css").glob("*.css")):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r'(?:src|href)="https?://|url\(https?://|@import', text), f.name
