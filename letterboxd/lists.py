"""/letterboxd/lists/* — a member list (metadata + ranked entries with the
owner's notes and ratings) and its comments."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd import refs
from letterboxd.comments import comments_page
from letterboxd.shared import fetch_page, finalize, page_block, path

ENTRIES_PER_PAGE = 100


def details(list, sort="list-order", page=1):
    """List metadata (owner, description, tags, film / like / clone /
    comment counts, dates) and one page of entries (100) with position,
    the owner's rating and notes."""
    base = fetch.list_path(list)
    missing = f"list {base.strip('/')} not found on Letterboxd"
    doc_path = path(base, "detail", refs.LIST_ENTRY_SORTS[sort], page=page)
    markup = fetch.html(doc_path, label="list", missing=missing) if page == 1 else None
    if markup is None:
        doc = fetch_page(doc_path, label="list", missing=missing)
        markup = str(doc)
    else:
        doc = P.soup(markup)
    meta = P.list_page(markup, base)
    entries = P.list_detail_entries(doc)
    # the comment count lives in the comments fragment (one small call)
    comments_count = None
    if meta.get("id"):
        try:
            frag = P.soup(fetch.html(f"/csi/list/{meta['id']}/comments-section/", ajax=True, label="list comments"))
            comments_count = P.comments_meta(frag)["count"]
        except (fetch.LetterboxdUpstreamError, fetch.LetterboxdNotFound):
            pass
    meta["comments_count"] = comments_count
    meta = P.ordered(meta, ["id", "slug", "name", "link", "short_link", "owner", "description",
                            "description_html", "tags", "is_ranked", "films_count", "likes_count",
                            "comments_count", "clones_count", "published_at", "updated_at"])
    return finalize({"list": meta, "sort": sort, "entries": entries,
                     "pagination": page_block(doc, page, ENTRIES_PER_PAGE, entries,
                                              total_count=meta.get("films_count"))})


def comments(list, page=1):
    """Comments on a list, newest first, 20 per page."""
    base = fetch.list_path(list)
    markup = fetch.html(base, label="list", missing=f"list {base.strip('/')} not found on Letterboxd")
    list_id = None
    doc = P.soup(markup)
    for node in doc.select("[data-src*='/csi/list/']"):
        segs = P.path_segments(node.get("data-src"))
        if len(segs) >= 3 and segs[2].isdigit():
            list_id = int(segs[2])
    if list_id is None:
        raise fetch.LetterboxdNotFound("this list has no public comments section")
    meta = P.list_page(markup, base)
    result = comments_page(f"/csi/list/{list_id}/comments-section/", f"/ajax/filmlist:{list_id}/comments/", page)
    return {"list": {"id": list_id, "slug": meta["slug"], "name": meta["name"], "link": meta["link"],
                     "owner": meta["owner"]}, **result}
