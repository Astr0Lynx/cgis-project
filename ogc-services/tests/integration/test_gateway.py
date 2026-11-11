# Integration tests: route mounting under the gateway prefixes /ogc/ and /tiles/.

from conftest import GATEWAY, OGC, TILES, get_json


# Yields every href string in a JSON document.
def all_hrefs(doc):
    if isinstance(doc, dict):
        for key, value in doc.items():
            if key == "href" and isinstance(value, str):
                yield value
            else:
                yield from all_hrefs(value)
    elif isinstance(doc, list):
        for value in doc:
            yield from all_hrefs(value)


# Every OGC link points back through the gateway's public prefixes.
def test_ogc_links_use_public_gateway_prefix(http):
    for url in (f"{OGC}/?f=json", f"{OGC}/collections?f=json",
                f"{OGC}/collections/synthetic-worldpop-2001/tiles/WebMercatorQuad?f=json"):
        hrefs = [h for h in all_hrefs(get_json(http, url)) if h.startswith("http://gateway")]
        assert hrefs and all(h.startswith(f"{OGC}/") or h.startswith(f"{OGC}?") or h.startswith(f"{TILES}/")
                             for h in hrefs), hrefs


# Vendor TileJSON tile URLs point back through /tiles/.
def test_tiles_links_use_public_gateway_prefix(http):
    tj = get_json(http, f"{TILES}/datasets/synthetic-worldpop-2001/WebMercatorQuad/tilejson.json")
    assert tj["tiles"][0].startswith(f"{TILES}/datasets/")


# /ogc and /tiles redirect (301) to the trailing-slash form.
def test_prefix_without_trailing_slash_redirects(http):
    for prefix in ("/ogc", "/tiles"):
        resp = http.get(f"{GATEWAY}{prefix}")
        assert resp.status_code == 301 and resp.headers["location"].endswith(f"{prefix}/")


# The HTML landing page renders.
def test_ogc_html_landing_page(http):
    resp = http.get(f"{OGC}/", params={"f": "html"})
    assert resp.status_code == 200 and "SYNTHETIC" in resp.text
