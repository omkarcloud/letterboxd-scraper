"""Comment threads on lists and reviews, newest first, 20 per page.

Letterboxd serves comments two ways and neither takes a page size:
  * /csi/<list|viewing>/<id>/comments-section/ — the NEWEST 20, plus
    `data-comments-start` = the index of the first of them;
  * /ajax/<filmlist|viewing>:<id>/comments/?start=N — EVERY comment from
    index N to the end, oldest first (a 34K-comment list is 7 MB+).
So page p asks for start = newest_start - (p-1)*20 and keeps the first 20
rows: the transfer grows with p, which is why pages are capped
(schemas.COMMENT_MAX_PAGE)."""
from letterboxd import fetch
from letterboxd import parsers as P

PER_PAGE = 20


def comments_page(section_path, ajax_path, page):
    section = P.soup(fetch.html(section_path, ajax=True, label="comments"))
    meta = P.comments_meta(section)
    newest_start = meta["start"] or 0
    total = meta["count"]
    if page == 1:
        rows = P.comments(section)
    else:
        start = newest_start - (page - 1) * PER_PAGE
        if start + PER_PAGE <= 0:
            rows = []
        else:
            doc = P.soup(fetch.html(ajax_path, {"start": max(start, 0)}, ajax=True, label="comments"))
            rows = P.comments(doc)[:PER_PAGE + min(start, 0)]
    rows = list(reversed(rows))                     # newest first
    reachable = newest_start + PER_PAGE
    total_pages = max((reachable + PER_PAGE - 1) // PER_PAGE, 1) if total else 1
    return {"comments": rows,
            "pagination": P.pagination(page, PER_PAGE, page < total_pages, total_count=total,
                                       total_pages=total_pages)}
