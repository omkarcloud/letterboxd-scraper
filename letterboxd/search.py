"""/letterboxd/search and /letterboxd/films/autocomplete. Search runs on
the site's /s/search/<type>/<query>/ fragment: 20 results per call, deeper
pages through the opaque `data-cursor` the fragment carries (Letterboxd has
no numeric search pages — /page/N/ is a 404)."""
from urllib.parse import quote

from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd.shared import expand_texts, finalize


def _parse(doc, type):
    rows = doc.select("ul.results > li.search-result, li.search-result")
    if type in ("films",):
        return [r for r in (P.search_film(li) for li in rows) if r]
    if type == "reviews":
        items = P.review_items(doc)
        expand_texts(items)
        return items
    if type in ("lists", "original-lists"):
        return P.list_summaries(doc)
    if type == "cast-crew":
        return [r for r in (P.search_contributor(li) for li in rows) if r]
    if type == "members":
        return [r for r in (P.search_member(li) for li in rows) if r]
    if type == "tags":
        return [r for r in (P.search_tag(li) for li in rows) if r]
    return []


def search(query, type="films", cursor=None):
    """Films, reviews, lists, original lists (no clones), cast & crew,
    members or tags matching `query` — 20 per call; pass `next_cursor` back
    as `cursor` for the next 20."""
    term = quote(query.replace("/", " "), safe="")
    # the cursor arrives decoded from the previous response; the site wants it percent-encoded
    params = {"cursor": cursor} if cursor else None
    markup = fetch.html(f"/s/search/{type}/{term}/", params, ajax=True, label=f"{type} search")
    doc = P.soup(markup)
    results = _parse(doc, type)
    nxt = P.search_cursor(doc) if results else None
    return finalize({"query": query, "type": type, "results": results,
                     "next_cursor": nxt, "has_more": bool(nxt)})


def autocomplete(query, limit=10):
    """Type-ahead film suggestions as the site's search box shows them."""
    data = fetch.json_get("/s/autocompletefilm", {"q": query, "limit": limit}, label="autocomplete")
    films = [P.film_json(item) for item in (data.get("data") or []) if isinstance(item, dict)]
    return {"query": query, "films": [f for f in films if f]}
