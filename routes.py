"""The 35 Letterboxd endpoints. Every path is served with and without the
`/letterboxd` prefix, so code generated against the hosted API (paths like
/films/details) runs unchanged against this server.

Params are validated by the same marshmallow schemas the hosted API uses
(letterboxd/schemas.py), so defaults, enums and error messages are
identical: unknown params are rejected, and ONE param per input accepts a
slug or a pasted letterboxd.com / boxd.it link (films also take an IMDb or
TMDB id). List endpoints page with `page` and return count / per_page /
current_page / total_pages / next / previous first, exactly like the hosted
API; search and the activity feed page with `cursor` / `next_cursor`.
"""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from letterboxd import browse, films, journal, lists, members, people, reviews, schemas, search

PREFIX = "/letterboxd"


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so a UTF-8 "Amélie" would arrive as "AmÃ©lie")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _page_link(params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    return f"{request.urlparts.scheme}://{request.urlparts.netloc}{request.path}?{urlencode(query)}"


def paginate(result, params):
    """Lift the scraper's `pagination` block into the flat shape the hosted
    API returns: count / per_page / current_page / total_pages / next /
    previous first, then the data."""
    pagination = result.pop("pagination", None) or {}
    page = int(pagination.get("page") or params.get("page") or 1)
    total_pages = int(pagination.get("total_pages") or 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(params, page + 1 if page < total_pages else None),
        "previous": _page_link(params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(path, schema, impl, paginated=False):
    """Validate the query with the endpoint's schema, run it, and map every
    failure the way the hosted API does: bad params -> 400, missing entity
    -> 404, upstream failure or block -> 502, anything else -> 500."""
    params = query_dict()
    data, error = load_query(schema, params)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"letterboxd rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"letterboxd blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"letterboxd {path} failed: {e}"}, 502)
    except Exception as e:
        return json_response({"error": f"letterboxd {path} failed: {type(e).__name__}: {e}"}, 500)
    return json_response(paginate(result, params) if paginated else result)


P = True   # paginated (page)

# (public path, request schema, endpoint function, paginated) — the order of the docs.
ENDPOINTS = [
    ("/films/details", schemas.FilmSchema, films.details, False),
    ("/films/autocomplete", schemas.AutocompleteSchema, search.autocomplete, False),
    ("/search", schemas.SearchSchema, search.search, False),
    ("/films/stats", schemas.FilmSchema, films.stats, False),
    ("/films/availability", schemas.AvailabilitySchema, films.availability, False),
    ("/films/reviews", schemas.FilmReviewsSchema, films.reviews, P),
    ("/films/lists", schemas.FilmListsSchema, films.lists, P),
    ("/films/members", schemas.FilmMembersSchema, films.members, P),
    ("/films/similar", schemas.FilmSchema, films.similar, False),
    ("/films/themes", schemas.FilmSchema, films.themes, False),
    ("/films/poster", schemas.PosterSchema, films.poster, False),
    ("/films/browse", schemas.BrowseSchema, browse.films, P),
    ("/films/collection", schemas.CollectionSchema, browse.collection, P),
    ("/people/details", schemas.PersonSchema, people.details, False),
    ("/tags", schemas.TagSchema, browse.tag, P),
    ("/reviews/details", schemas.ReviewSchema, reviews.details, False),
    ("/reviews/comments", schemas.ReviewCommentsSchema, reviews.comments, P),
    ("/reviews/popular", schemas.PopularReviewsSchema, browse.popular_reviews, P),
    ("/lists/details", schemas.ListSchema, lists.details, P),
    ("/lists/comments", schemas.ListCommentsSchema, lists.comments, P),
    ("/lists/popular", schemas.PeriodSchema, browse.popular_lists, P),
    ("/members/details", schemas.MemberSchema, members.details, False),
    ("/members/films", schemas.MemberFilmsSchema, members.films, P),
    ("/members/diary", schemas.DiarySchema, members.diary, P),
    ("/members/reviews", schemas.MemberReviewsSchema, members.reviews, P),
    ("/members/watchlist", schemas.WatchlistSchema, members.watchlist, P),
    ("/members/lists", schemas.MemberListsSchema, members.lists, P),
    ("/members/likes", schemas.MemberLikesSchema, members.likes, P),
    ("/members/followers", schemas.MemberPageSchema, members.followers, P),
    ("/members/following", schemas.MemberPageSchema, members.following, P),
    ("/members/activity", schemas.ActivitySchema, members.activity, False),
    ("/members/stats", schemas.MemberStatsSchema, members.stats, False),
    ("/members/tags", schemas.MemberTagsSchema, members.tags, P),
    ("/members/popular", schemas.PeriodSchema, browse.popular_members, P),
    ("/journal", schemas.EmptySchema, journal.latest, False),
]


def mount(path, schema, impl, paginated):
    """Serve one endpoint at /path and /letterboxd/path."""
    def handler():
        return call(path, schema, impl, paginated)
    handler.__name__ = "letterboxd_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route(PREFIX + path, method="GET")(handler)


for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
