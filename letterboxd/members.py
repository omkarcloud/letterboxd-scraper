"""/letterboxd/members/* — profiles, logged films, diary, reviews,
watchlist, lists, likes, followers / following, stats, activity feed and
tags of a Letterboxd member."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd import refs
from letterboxd.shared import (drop_keys, expand_texts, fetch_page, finalize, page_block, page_member,
                               path, rating_segment)

FILMS_PER_PAGE = 72
DIARY_PER_PAGE = 50
REVIEWS_PER_PAGE = 12
WATCHLIST_PER_PAGE = 28
LISTS_PER_PAGE = 12
PEOPLE_PER_PAGE = 25
ACTIVITY_PER_CALL = 20
TAGS_PER_PAGE = 120


def _missing(username):
    return f"member '{username}' not found on Letterboxd"


def details(member):
    """Profile: name, avatar, badge (patron / pro / hq), bio, location,
    website and social links, film / list / follower counts, favourite
    films, ratings histogram and recently logged films."""
    username = member["username"]
    markup = fetch.html(f"/{username}/", label=f"member {username}", missing=_missing(username))
    out = P.profile(markup, username)
    bio_url = out.get("_bio_full_text")
    if bio_url:
        try:
            out["bio"], out["bio_html"] = P.full_text(fetch.html(bio_url, ajax=True, label="bio"))
        except (fetch.LetterboxdUpstreamError, fetch.LetterboxdNotFound):
            pass
    return finalize(out)


def films(member, sort="release", rating=None, genres=None, decade=None, year=None, page=1):
    """Films the member has logged (watched), 72 per page, with the
    member's rating and like."""
    username = member["username"]
    rated = f"rated/{rating_segment(rating)}" if rating is not None else None
    genre = f"genre/{refs.genre_segment(genres)}" if genres else None
    period = f"decade/{decade}" if decade else (f"year/{year}" if year else None)
    doc = fetch_page(path(username, "films", rated, period, genre, refs.MEMBER_FILM_SORTS[sort], page=page),
                     label="member films", missing=_missing(username))
    items = P.grid_films(doc)
    return finalize({"member": page_member(doc, username), "sort": sort, "films": items,
                     "pagination": page_block(doc, page, FILMS_PER_PAGE, items)})


def diary(member, year=None, month=None, page=1):
    """Diary entries (watch date, rating, like, rewatch, review), newest
    first, 50 per page; `year` / `month` narrow it."""
    username = member["username"]
    period = None
    if year:
        period = f"for/{year}" + (f"/{month:02d}" if month else "")
    doc = fetch_page(path(username, "diary/films", period, page=page), label="diary", missing=_missing(username))
    items = P.diary_rows(doc)
    return finalize({"member": page_member(doc, username), "year": year, "month": month, "entries": items,
                     "pagination": page_block(doc, page, DIARY_PER_PAGE, items)})


def reviews(member, sort="newest", year=None, page=1):
    """The member's reviews, 12 per page, full text."""
    username = member["username"]
    period = f"for/{year}" if year else None
    doc = fetch_page(path(username, "reviews/films", period, refs.MEMBER_REVIEW_SORTS[sort], page=page),
                     label="member reviews", missing=_missing(username))
    items = drop_keys(P.review_items(doc), "member")
    expand_texts(items)
    return finalize({"member": page_member(doc, username), "sort": sort, "reviews": items,
                     "pagination": page_block(doc, page, REVIEWS_PER_PAGE, items)})


def watchlist(member, sort="added", genres=None, decade=None, year=None, page=1):
    """Films on the member's watchlist, 28 per page."""
    username = member["username"]
    genre = f"genre/{refs.genre_segment(genres)}" if genres else None
    period = f"decade/{decade}" if decade else (f"year/{year}" if year else None)
    doc = fetch_page(path(username, "watchlist", period, genre, refs.WATCHLIST_SORTS[sort], page=page),
                     label="watchlist", missing=_missing(username))
    items = P.grid_films(doc)
    total = None
    count_node = doc.select_one(".js-watchlist-count, .watchlist-count")
    # the header count is the whole watchlist, not the genre / period-filtered view
    if count_node is not None and not (genres or period):
        total = P.count(P.clean(count_node))
    return finalize({"member": page_member(doc, username), "sort": sort, "films": items,
                     "pagination": page_block(doc, page, WATCHLIST_PER_PAGE, items, total_count=total)})


def lists(member, sort="updated", page=1):
    """Lists the member created, 12 per page."""
    username = member["username"]
    doc = fetch_page(path(username, "lists", refs.MEMBER_LIST_SORTS[sort], page=page),
                     label="member lists", missing=_missing(username))
    items = drop_keys(P.list_summaries(doc), "owner")
    return finalize({"member": page_member(doc, username), "sort": sort, "lists": items,
                     "pagination": page_block(doc, page, LISTS_PER_PAGE, items)})


def likes(member, type="films", page=1):
    """What the member liked: films (72/page), reviews or lists (12/page)."""
    username = member["username"]
    doc = fetch_page(path(username, "likes", type, page=page), label=f"liked {type}", missing=_missing(username))
    out = {"member": page_member(doc, username), "type": type}
    if type == "films":
        items = P.grid_films(doc)
        out["films"] = items
        per = FILMS_PER_PAGE
    elif type == "reviews":
        items = P.review_items(doc)
        expand_texts(items)
        out["reviews"] = items
        per = REVIEWS_PER_PAGE
    else:
        items = P.list_summaries(doc)
        out["lists"] = items
        per = LISTS_PER_PAGE
    out["pagination"] = page_block(doc, page, per, items)
    return finalize(out)


def _people(member, kind, page):
    username = member["username"]
    doc = fetch_page(path(username, kind, page=page), label=kind, missing=_missing(username))
    items = P.member_rows(doc)
    return finalize({"member": page_member(doc, username), "members": items,
                     "pagination": page_block(doc, page, PEOPLE_PER_PAGE, items)})


def followers(member, page=1):
    """Members following this member, 25 per page."""
    return _people(member, "followers", page)


def following(member, page=1):
    """Members this member follows, 25 per page."""
    return _people(member, "following", page)


def activity(member, cursor=None):
    """The member's activity feed (watched, rated, reviewed, liked, listed,
    commented, added to watchlist, followed), 20 per call, newest first."""
    username = member["username"]
    params = {"after": cursor} if cursor else None
    markup = fetch.html(f"/ajax/activity-pagination/{username}/", params, ajax=True,
                        label="activity", missing=_missing(username))
    items = P.activity(P.soup(markup))
    nxt = str(items[-1]["id"]) if len(items) >= ACTIVITY_PER_CALL - 1 and items[-1].get("id") else None
    return {"username": username, "activity": items, "next_cursor": nxt, "has_more": bool(nxt)}


def stats(member, year=None):
    """All-time stats (or one year's): headline numbers, most-watched and
    highest-rated genres / countries / languages, most-watched and
    highest-rated actors and directors, most rewatched films and highest
    rated decades — the stats page's fragments fetched in parallel."""
    username = member["username"]
    if year:
        page_path = f"/{username}/year/{year}/"
        base = f"/csi/person/{username}/year/{year}"
    else:
        page_path = f"/{username}/stats/"
        base = f"/csi/person/{username}/stats"
    calls = {
        "page": lambda: fetch.html(page_path, label="stats", missing=f"no stats for member '{username}'"
                                   + (f" in {year}" if year else "")),
        "genres": lambda: fetch.html(f"{base}/genres/", ajax=True, label="stats genres"),
        "actors_most": lambda: fetch.html(f"{base}/actors-most-watched/p/0/", ajax=True, label="actors"),
        "actors_best": lambda: fetch.html(f"{base}/actors-highest-rated/p/0/", ajax=True, label="actors"),
        "directors_most": lambda: fetch.html(f"{base}/directors-most-watched/p/0/", ajax=True, label="directors"),
        "directors_best": lambda: fetch.html(f"{base}/directors-highest-rated/p/0/", ajax=True, label="directors"),
        "highs": lambda: fetch.html(f"{base}/highs-and-lows/", ajax=True, label="stats highs"),
    }
    if not year:
        calls["decades"] = lambda: fetch.html(f"{base}/decades/", ajax=True, label="stats decades")
    keys = list(calls)
    first = fetch.run_parallel([calls["page"]])[0]
    rest = fetch.run_parallel_quiet([calls[k] for k in keys[1:]])
    got = dict(zip(keys[1:], rest))
    doc = P.soup(first)
    breakdown = P.breakdown(got.get("genres") or "")
    out = {
        "member": page_member(doc, username),
        "year": year,
        "summary": P.summary_statistics(doc),
        "most_watched": {
            **(breakdown.get("most_watched") or {"genres": [], "countries": [], "languages": []}),
            "actors": P.person_leaders(got.get("actors_most") or ""),
            "directors": P.person_leaders(got.get("directors_most") or ""),
            "films": P.most_watched_films(got.get("highs") or ""),
        },
        "highest_rated": {
            **(breakdown.get("highest_rated") or {"genres": [], "countries": [], "languages": []}),
            "actors": P.person_leaders(got.get("actors_best") or ""),
            "directors": P.person_leaders(got.get("directors_best") or ""),
            "decades": P.decades(got.get("decades") or ""),
        },
    }
    return finalize(out)


def tags(member, type="films", page=1):
    """Tags the member uses on films (diary / reviews), lists or reviews,
    with how often each is used."""
    username = member["username"]
    doc = fetch_page(path(username, "tags", type, page=page), label="tags", missing=_missing(username))
    items = P.member_tags(doc)
    return {"member": finalize(page_member(doc, username)), "type": type, "tags": items,
            "pagination": page_block(doc, page, TAGS_PER_PAGE, items)}
