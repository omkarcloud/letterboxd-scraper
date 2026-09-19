"""Helpers shared by the Letterboxd endpoint modules: URL building, page
fetching + pagination blocks, poster attachment, truncated-text expansion
and the top-level member card of member-scoped pages."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd.refs import SITE, quote_segment


def path(*parts, page=1):
    """('/dave/films', 'rated/4', '', 'by/name') -> '/dave/films/rated/4/by/name/[page/N/]'."""
    segs = []
    for part in parts:
        if part:
            segs.extend(s for s in str(part).split("/") if s)
    tail = f"page/{page}/" if page and page > 1 else ""
    return "/" + "/".join(segs) + "/" + tail


def rating_segment(rating):
    """4.0 -> '4'; 3.5 -> '3½'; 0.5 -> '½' (the site's rated/<x>/ syntax)."""
    if rating is None:
        return None
    whole = int(rating)
    half = "½" if rating - whole >= 0.5 else ""
    return f"{whole if whole else ''}{half}" or None


def page_block(doc, page, per_page, items, total_count=None):
    """Pagination block from a paged page: total pages from the page list
    when shown, else "this page + 1" while a Next link exists."""
    total_pages = P.pages_total(doc)
    more = P.has_next(doc)
    if total_pages is not None:
        total_pages = max(total_pages, page)
    elif not items and page > 1:
        total_pages = page - 1
    return P.pagination(page, per_page, more, total_count=total_count, total_pages=total_pages)


def fetch_page(page_path, *, label, missing=None, params=None, ajax=False):
    """GET a page -> BeautifulSoup doc. A page past the end (404 on page/N/)
    becomes an empty doc so callers return an empty page, not a 404."""
    try:
        return P.soup(fetch.html(page_path, params, label=label, missing=missing, ajax=ajax))
    except fetch.LetterboxdNotFound:
        if "/page/" in page_path:
            return P.soup("")
        raise


# ---- posters / internal keys -----------------------------------------------------------

def _walk(obj, found):
    if isinstance(obj, dict):
        if "_poster" in obj:
            found.append(obj)
        for value in obj.values():
            _walk(value, found)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, found)


def _strip_private(obj):
    if isinstance(obj, dict):
        for key in [k for k in obj if k.startswith("_")]:
            obj.pop(key)
        for value in obj.values():
            _strip_private(value)
    elif isinstance(obj, list):
        for value in obj:
            _strip_private(value)
    return obj


def finalize(result, posters=True):
    """Fill every film card's `poster` ({link 500x750, large_link
    1000x1500} or None) with one parallel /poster/std/ fan-out (memoised),
    then drop the parser's private `_…` keys."""
    cards = []
    _walk(result, cards)
    if posters and cards:
        found = fetch.posters([tuple(c["_poster"]) for c in cards if c.get("_poster")])
        for card in cards:
            uid = (card.get("_poster") or (None,))[0]
            card["poster"] = found.get(uid) or None
    return _strip_private(result)


# ---- truncated texts -----------------------------------------------------------------

def expand_texts(items, key="_full_text"):
    """Items whose text the page truncated (key = its /s/full-text/ path)
    get the full text + HTML, fetched in parallel."""
    todo = [item for item in items if item and item.get(key)]
    results = fetch.run_parallel_quiet(
        [lambda i=i: P.full_text(fetch.html(i[key], ajax=True, label="full text")) for i in todo])
    for item, got in zip(todo, results):
        if got and got[0]:
            item["text"], item["html"] = got
            if "is_text_truncated" in item:
                item["is_text_truncated"] = False
    return items


# ---- member-scoped pages -----------------------------------------------------------------

def page_member(doc, username):
    """The member a member-scoped page belongs to (its header avatar link)."""
    for a in doc.select("a.avatar[href]"):
        segs = P.path_segments(a.get("href"))
        if segs == [username]:
            member = P.member_from_link(a)
            if member:
                return member
    title = doc.find("meta", property="og:title")
    name = None
    if title is not None and title.get("content"):
        name = title["content"].split("’s")[0].split("'s")[0].strip() or None
    return {"username": username, "name": name or username, "link": f"{SITE}/{username}/", "avatar": None}


def drop_keys(items, *keys):
    for item in items:
        for key in keys:
            item.pop(key, None)
    return items


__all__ = ["path", "rating_segment", "page_block", "fetch_page", "finalize", "expand_texts",
           "page_member", "drop_keys", "quote_segment"]
