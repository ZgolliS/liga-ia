"""Le référencement (SEO) et la visibilité dans les moteurs de réponse IA (GEO)."""

import json
import re
import xml.etree.ElementTree as ET

from tests.test_web import client  # noqa: F401  (même site de test)


def jsonld(html: str) -> list[dict]:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    return [json.loads(b) for b in blocks]


def test_each_page_has_unique_title_description_and_canonical(client) -> None:  # noqa: F811
    seen = set()
    for path in ["/", "/classement", "/matchs?journee=1", "/mercato", "/equipe/1", "/joueur/1"]:
        html = client.get(path).text
        title = re.search(r"<title>(.*?)</title>", html, re.DOTALL).group(1)
        desc = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
        assert title not in seen and len(desc) > 40, path
        seen.add(title)
        assert '<link rel="canonical" href="http://testserver' in html
        assert 'property="og:title"' in html and 'property="og:image"' in html


def test_structured_data_is_valid_json_and_describes_the_page(client) -> None:  # noqa: F811
    team = jsonld(client.get("/equipe/1").text)
    types = {b["@type"] for b in team}
    assert {"SportsTeam", "BreadcrumbList"} <= types
    player = jsonld(client.get("/joueur/1").text)
    person = next(b for b in player if b["@type"] == "Person")
    assert person["name"] == "Unai Simón" and person["birthDate"]
    assert {b["@type"] for b in jsonld(client.get("/").text)} == {"WebSite"}


def test_factual_summary_is_visible_for_ai_engines(client) -> None:  # noqa: F811
    html = client.get("/").text
    assert 'class="summary"' in html and "est en tête de la Liga avec" in html


def test_robots_allows_search_and_ai_bots_but_not_api(client) -> None:  # noqa: F811
    txt = client.get("/robots.txt").text
    assert "User-agent: *\nAllow: /\nDisallow: /api/" in txt
    for bot in ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"]:
        assert f"User-agent: {bot}" in txt
    assert "Sitemap: http://testserver/sitemap.xml" in txt


def test_sitemap_lists_pages_teams_and_players(client) -> None:  # noqa: F811
    r = client.get("/sitemap.xml")
    assert r.headers["content-type"].startswith("application/xml")
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text for e in ET.fromstring(r.text).findall("s:url/s:loc", ns)]
    assert "http://testserver/classement" in locs
    assert any("/equipe/" in u for u in locs) and any("/joueur/" in u for u in locs)


def test_llms_txt_presents_the_site(client) -> None:  # noqa: F811
    txt = client.get("/llms.txt").text
    assert txt.startswith("# Liga IA") and "## Équipes" in txt and "/classement" in txt


def test_error_pages_are_not_indexed(client) -> None:  # noqa: F811
    html = client.get("/page-inexistante").text
    assert '<meta name="robots" content="noindex">' in html
    assert 'rel="canonical"' not in html


def test_static_files_are_cached_and_pages_compressed(client) -> None:  # noqa: F811
    css = client.get("/static/style.css")
    assert "max-age=604800" in css.headers["cache-control"]
    page = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert page.headers.get("content-encoding") == "gzip"


def test_french_plurals() -> None:
    from foot.web.seo import plural

    assert (plural(0, "défaite"), plural(1, "défaite"), plural(2, "défaite")) == (
        "0 défaite", "1 défaite", "2 défaites")
