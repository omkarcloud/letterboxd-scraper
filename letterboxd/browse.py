"""/letterboxd/browse/* — the film browser (popular / highest rated /
newest, filtered by genre, decade or year, country, language, streaming
service, theme or nanogenre), film collections, popular lists, members and
reviews, and tag pages."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd import refs
from letterboxd.shared import (expand_texts, fetch_page, finalize, page_block, path,
                               quote_segment)

BROWSE_PER_PAGE = 72
POPULAR_LISTS_PER_PAGE = 18
POPULAR_MEMBERS_PER_PAGE = 30
POPULAR_REVIEWS_PER_PAGE = 12
TAG_FILMS_PER_PAGE = 72
TAG_LISTS_PER_PAGE = 12
TAG_REVIEWS_PER_PAGE = 12


def films(sort="popular", genres=None, decade=None, year=None, country=None, language=None,
          service=None, theme=None, page=1):
    """One page (72) of Letterboxd's film browser, with each film's average
    rating. Filters combine; `genres` takes several (prefix '-' to exclude)."""
    parts = [refs.BROWSE_SORTS[sort]]
    if theme:
        parts.append(f"{theme['kind']}/{theme['slug']}")
    if genres:
        parts.append(f"genre/{refs.genre_segment(genres)}")
    if decade:
        parts.append(f"decade/{decade}")
    if year:
        parts.append(f"year/{year}")
    if country:
        parts.append(f"country/{country}")
    if language:
        parts.append(f"language/{language}")
    if service:
        parts.append(f"on/{service}")
    doc = fetch_page(path("csi/films/films-browser-list", *parts, page=page), label="film browser", ajax=True)
    items = P.grid_films(doc)
    total = P.browse_total(doc)
    heading = P.clean(doc.select_one(".ui-block-heading")) or ""
    if service and heading.startswith("There ") and "available on" not in heading:
        # an unknown /on/<service>/ slug is silently dropped by the site,
        # which then serves the unfiltered catalogue
        raise ValueError(f"Unknown streaming service '{service}' (use the slug from a "
                         f"letterboxd.com/films/on/<service>/ link, e.g. amazon-usa).")
    return finalize({
        "filters": {"sort": sort, "genres": genres or [], "decade": decade, "year": year,
                    "country": country, "language": language, "service": service,
                    "theme": theme},
        "films": items,
        "pagination": page_block(doc, page, BROWSE_PER_PAGE, items, total_count=total),
    })


def collection(collection, sort="release-earliest", page=1):
    """A film collection / franchise (e.g. toy-story-collection): its name
    and films in release order (or any browse sort)."""
    order = refs.BROWSE_SORTS[sort]
    page_html, grid = fetch.run_parallel([
        lambda: fetch.html(f"/films/in/{collection}/", label="collection",
                           missing=f"collection '{collection}' not found on Letterboxd"),
        lambda: fetch.html(path("csi/films/films-browser-list", order, "in", collection, page=page),
                           ajax=True, label="collection films"),
    ])
    names = [P.clean(h) for h in P.soup(page_html).select("h1") if not h.select_one("a.logo")]
    doc = P.soup(grid)
    items = P.grid_films(doc)
    return finalize({
        "collection": {"slug": collection, "name": names[0] if names else None,
                       "link": f"{refs.SITE}/films/in/{collection}/"},
        "sort": sort,
        "films": items,
        "pagination": page_block(doc, page, BROWSE_PER_PAGE, items, total_count=P.browse_total(doc)),
    })


def popular_lists(period="all-time", page=1):
    """The most popular member lists, all time or this week / month / year."""
    doc = fetch_page(path("lists/popular", refs.PERIODS[period], page=page), label="popular lists")
    items = P.list_summaries(doc)
    return finalize({"period": period, "lists": items,
                     "pagination": page_block(doc, page, POPULAR_LISTS_PER_PAGE, items)})


def popular_members(period="all-time", page=1):
    """The most followed / active members, with films, lists and likes counts."""
    doc = fetch_page(path("members/popular", refs.PERIODS[period], page=page), label="popular members")
    items = P.member_rows(doc)
    return finalize({"period": period, "members": items,
                     "pagination": page_block(doc, page, POPULAR_MEMBERS_PER_PAGE, items)})


def popular_reviews(period="week", page=1):
    """The most liked reviews across the site, this week / month / year or
    all time; truncated reviews are expanded."""
    doc = fetch_page(path("reviews/popular", refs.PERIODS[period], page=page), label="popular reviews")
    items = P.review_items(doc)
    expand_texts(items)
    return finalize({"period": period, "reviews": items,
                     "pagination": page_block(doc, page, POPULAR_REVIEWS_PER_PAGE, items)})


def tag(tag, type="films", sort="popular", page=1):
    """Everything members tagged with a tag: films (72/page), lists or
    reviews (12/page)."""
    seg = quote_segment(tag)
    order = refs.TAG_FILM_SORTS[sort] if type == "films" else None
    doc = fetch_page(path("tag", seg, type, order, page=page), label=f"tag {tag}",
                     missing=f"tag '{tag}' not found on Letterboxd")
    out = {"tag": {"slug": tag, "link": f"{refs.SITE}/tag/{seg}/"}, "type": type,
           "sort": sort if type == "films" else None}
    if type == "films":
        items = P.grid_films(doc)
        out["films"] = items
        per = TAG_FILMS_PER_PAGE
    elif type == "lists":
        items = P.list_summaries(doc)
        out["lists"] = items
        per = TAG_LISTS_PER_PAGE
    else:
        items = P.review_items(doc)
        expand_texts(items)
        out["reviews"] = items
        per = TAG_REVIEWS_PER_PAGE
    out["pagination"] = page_block(doc, page, per, items)
    return finalize(out)
