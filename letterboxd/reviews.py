"""/letterboxd/reviews/* — one review / diary entry in full, and its
comments."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd.comments import comments_page
from letterboxd.shared import finalize


def _missing(path):
    return f"review {path.strip('/')} not found on Letterboxd (private, deleted or never logged)"


def details(review):
    """A review or diary entry: author, film, rating, like, rewatch,
    watched date, spoiler flag, tags, full text + HTML, likes and comments
    counts."""
    page_path = fetch.review_path(review)
    page = P.review_page(fetch.html(page_path, label="review", missing=_missing(page_path)))
    viewing_id = page.get("viewing_id")
    if not viewing_id:
        raise fetch.LetterboxdNotFound(_missing(page_path))
    def optional(fn):
        def call():
            try:
                return fn()
            except (fetch.LetterboxdUpstreamError, fetch.LetterboxdNotFound):
                return None
        return call

    # the entry JSON is essential (rating, dates, flags) — its failure fails the call
    data, text, section = fetch.run_parallel([
        lambda: fetch.json_get(f"/viewing/{viewing_id}/json/", label="review data"),
        optional(lambda: fetch.html(f"/s/full-text/viewing:{viewing_id}/", ajax=True, label="review text")),
        optional(lambda: fetch.html(f"/csi/viewing/{viewing_id}/comments-section/", ajax=True, label="comments")),
    ])
    entry = P.viewing_json(data) or {}
    review_text, review_html = P.full_text(text) if text else (None, None)
    comments_count = P.comments_meta(P.soup(section))["count"] if section else None
    film = entry.get("film") or {}
    card = None
    if film.get("id"):
        card = {"id": film["id"], "slug": film["slug"], "name": film["name"], "year": film["year"],
                "link": film["link"], "short_link": film["short_link"], "poster": None,
                "_poster": (f"film:{film['id']}", f"/film/{film['slug']}/", None)}
    out = {
        "id": viewing_id,
        "link": P.absolute(page_path),
        "short_link": page.get("short_link") or entry.get("short_link"),
        "member": page.get("member"),
        "film": card,
        "rating": entry.get("rating"),
        "is_liked": entry.get("is_liked", False),
        "is_rewatch": entry.get("is_rewatch", page.get("is_rewatch", False)),
        "watched_date": entry.get("watched_date") or page.get("watched_date"),
        "has_spoilers": entry.get("has_spoilers", False),
        "tags": entry.get("tags") or page.get("tags") or [],
        "text": review_text,
        "html": review_html,
        "has_review": bool(review_text),
        "likes_count": page.get("likes_count"),
        "comments_count": comments_count,
    }
    return finalize(out)


def comments(review, page=1):
    """Comments on a review, newest first, 20 per page."""
    page_path = fetch.review_path(review)
    info = P.review_page(fetch.html(page_path, label="review", missing=_missing(page_path)))
    viewing_id = info.get("viewing_id")
    if not viewing_id:
        raise fetch.LetterboxdNotFound(_missing(page_path))
    result = comments_page(f"/csi/viewing/{viewing_id}/comments-section/",
                           f"/ajax/viewing:{viewing_id}/comments/", page)
    return {"review": {"id": viewing_id, "link": P.absolute(page_path), "member": info.get("member")}, **result}
