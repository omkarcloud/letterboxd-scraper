"""/letterboxd/films/* — film details, stats, where to watch, reviews,
lists, members / fans / likes, similar films, themes and poster art."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd import refs
from letterboxd.shared import (drop_keys, expand_texts, fetch_page, finalize, page_block, path,
                               rating_segment)

REVIEWS_PER_PAGE = 12
LISTS_PER_PAGE = 12
MEMBERS_PER_PAGE = 25


def _film_card(doc, slug):
    """The film a film sub-page is about (its sidebar / header poster)."""
    for film, _ in P.film_cards(doc):
        if film.get("slug") == slug:
            return film
    return {"id": None, "slug": slug, "name": None, "year": None,
            "link": f"{refs.SITE}/film/{slug}/", "short_link": None, "poster": None}


def details(film):
    """Everything the film page shows: titles, synopsis, cast with
    characters, crew by role, studios, countries, languages, genres, themes,
    releases by country, trailer, IMDb / TMDB ids, similar films — plus the
    stats fragment (watches, lists, likes, Top 500 rank) and the ratings
    histogram, fetched in parallel."""
    slug = fetch.film_slug(film)
    base = f"/film/{slug}/"
    page, stats, hist = fetch.run_parallel([
        lambda: fetch.html(base, label=f"film {slug}", missing=f"film '{slug}' not found on Letterboxd"),
        lambda: fetch.html(f"/csi{base}stats/", ajax=True, label="film stats"),
        lambda: fetch.html(f"/csi{base}rating-histogram/", ajax=True, label="ratings histogram"),
    ])
    out = P.film_page(page)
    hist = P.histogram(hist) or {}
    out["rating"] = {
        "average": out["rating"]["average"] if out["rating"]["average"] is not None else hist.get("average"),
        "count": hist.get("count") or out["rating"]["count"],
        "distribution": hist.get("distribution") or [],
    }
    out["stats"] = P.film_stats(stats)
    ld_image = out.pop("_ld_image", None)
    poster = fetch.posters([(f"film:{out['id']}", base, None)]).get(f"film:{out['id']}") if out.get("id") else None
    out["poster"] = poster or ({"link": ld_image, "large_link": None} if ld_image else None)
    out = P.ordered(out, ["id", "slug", "name", "original_name", "year", "link", "short_link", "tagline",
                          "synopsis", "runtime_minutes", "release_date", "rating", "stats", "reviews_count"])
    return finalize(out)


def stats(film):
    """Lightweight counters: average rating + histogram, watches, lists,
    likes and the rank in Letterboxd's Top 500."""
    slug = fetch.film_slug(film)
    info, stats_html, hist = fetch.run_parallel([
        lambda: fetch.json_get(f"/film/{slug}/json/", label=f"film {slug}", missing=f"film '{slug}' not found on Letterboxd"),
        lambda: fetch.html(f"/csi/film/{slug}/stats/", ajax=True, label="film stats"),
        lambda: fetch.html(f"/csi/film/{slug}/rating-histogram/", ajax=True, label="ratings histogram"),
    ])
    card = P.film_json(info) or {"slug": slug}
    return {"film": card, "rating": P.histogram(hist), "stats": P.film_stats(stats_html)}


def availability(film, country="US", format="best"):
    """Where to stream / rent / buy in one country (JustWatch data as the
    site shows it): service, direct link, price + currency."""
    slug = fetch.film_slug(film)
    info = fetch.json_get(f"/film/{slug}/json/", label=f"film {slug}",
                          missing=f"film '{slug}' not found on Letterboxd")
    card = P.film_json(info)
    locale = refs.availability_locale(country)
    data = fetch.json_get("/s/production-availabilities",
                          {"productionUID": f"film:{card['id']}", "locale": locale}, label="availability")
    groups = P.availability(data)
    chosen = groups.get(format) or {"stream": [], "rent": [], "buy": []}
    return {"film": card, "country": country.upper(), "format": format, **chosen}


def reviews(film, sort="popular", rating=None, page=1):
    """Member reviews of a film, 12 per page; truncated reviews are
    expanded to their full text."""
    slug = fetch.film_slug(film)
    rated = f"rated/{rating_segment(rating)}" if rating is not None else None
    doc = fetch_page(path("film", slug, "reviews", rated, refs.FILM_REVIEW_SORTS[sort], page=page),
                     label="film reviews", missing=f"film '{slug}' not found on Letterboxd")
    card = _film_card(doc, slug)
    items = drop_keys(P.review_items(doc), "film")
    expand_texts(items)
    return finalize({"film": card, "sort": sort, "rating": rating, "reviews": items,
                     "pagination": page_block(doc, page, REVIEWS_PER_PAGE, items)})


def lists(film, sort="popular", page=1):
    """Member lists that include the film, 12 per page."""
    slug = fetch.film_slug(film)
    doc = fetch_page(path("film", slug, "lists", refs.FILM_LIST_SORTS[sort], page=page),
                     label="film lists", missing=f"film '{slug}' not found on Letterboxd")
    items = P.list_summaries(doc)
    return finalize({"film": _film_card(doc, slug), "sort": sort, "lists": items,
                     "pagination": page_block(doc, page, LISTS_PER_PAGE, items)})


def members(film, type="watched", sort="newest", rating=None, page=1):
    """Members who watched (with their rating, like and review), have it
    as a favourite (fans) or liked it — 25 per page."""
    slug = fetch.film_slug(film)
    rated = f"rated/{rating_segment(rating)}" if rating is not None and type == "watched" else None
    order = refs.FILM_MEMBER_SORTS[sort] if type != "fans" else None
    doc = fetch_page(path("film", slug, refs.FILM_MEMBER_KINDS[type], rated, order, page=page),
                     label=f"film {type}", missing=f"film '{slug}' not found on Letterboxd")
    items = P.member_rows(doc)
    return finalize({"film": _film_card(doc, slug), "type": type, "sort": sort if type != "fans" else None,
                     "members": items, "pagination": page_block(doc, page, MEMBERS_PER_PAGE, items)})


def similar(film):
    """Letterboxd's "similar films" for a film (up to 72)."""
    slug = fetch.film_slug(film)
    doc = fetch_page(path("film", slug, "similar"), label="similar films",
                     missing=f"film '{slug}' not found on Letterboxd")
    films = [f for f in P.grid_films(doc) if f.get("slug") != slug]
    return finalize({"film": _film_card(doc, slug), "films": films})


def themes(film):
    """The film's themes / mini-themes / nanogenres, each with its closest
    films (the site's "Show all themes" page)."""
    slug = fetch.film_slug(film)
    doc = fetch_page(path("film", slug, "themes"), label="film themes",
                     missing=f"film '{slug}' not found on Letterboxd")
    sections = []
    for h2 in doc.select("section h2"):
        a = h2.select_one("a[href]")
        if a is None:
            continue
        segs = P.path_segments(a.get("href"))
        kind = next((s for s in segs if s in refs.THEME_KINDS), None)
        if kind is None:
            continue
        theme_slug = segs[segs.index(kind) + 1] if segs.index(kind) + 1 < len(segs) else None
        block = h2.find_parent("section")
        films = [f for f in (P.grid_film(li) for li in block.select("li.posteritem, li.griditem")) if f] \
            if block is not None else []
        sections.append({"name": P.clean(a), "type": kind, "slug": theme_slug,
                         "link": P.absolute(f"/films/{kind}/{theme_slug}/"), "films": films})
    return finalize({"film": _film_card(doc, slug), "themes": sections})


def poster(film, width=500):
    """Poster art at a chosen width (height = 1.5 x width) plus the 2x link."""
    slug = fetch.film_slug(film)
    info = fetch.json_get(f"/film/{slug}/json/", label=f"film {slug}",
                          missing=f"film '{slug}' not found on Letterboxd")
    data = fetch.json_get(f"/film/{slug}/poster/std/{width}/", label="poster",
                          missing="no poster at that size")
    return {"film": P.film_json(info), "width": width, "height": int(width * 1.5),
            "link": data.get("url"), "large_link": data.get("url2x")}
