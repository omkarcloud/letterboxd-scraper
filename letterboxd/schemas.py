"""Marshmallow request schemas for every /letterboxd/* route.

Generic fields come from the shared top-level schema_fields.py; this module
adds the Letterboxd resolvers and the per-route schemas. Every schema's
load() output is the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url` / `id` pair) — refs.py lists every accepted form:
  film        slug | letterboxd.com film link | boxd.it link | IMDb id / link | tmdb:<id> / TMDB link
  member      username | profile link
  list        <member>/<list-slug> | list link | boxd.it link
  review      <member>/<film-slug>[/<n>] | review link | boxd.it link
  person      slug | <role>/<slug> | person link
  collection  slug | /films/in/<slug>/ link
  theme       slug | mini-theme/<slug> | nanogenre/<slug> | theme link
  tag         tag text | /tag/<tag>/ link
  country     name | Letterboxd slug | ISO 3166 code
  language    name | Letterboxd slug | ISO 639-1 code

Pagination follows what Letterboxd itself allows:
  * `page` on every paged list (page sizes are the site's: 72 films, 50
    diary entries, 28 watchlist films, 25 members, 20 comments, 18 or 12
    lists / reviews, 100 list entries) — responses carry count /
    total_pages / next;
  * `cursor` where the site only hands out opaque cursors (search, a
    member's activity feed) — responses carry next_cursor.
"""
from datetime import date

from marshmallow import ValidationError, fields, validate, validates_schema

from schema_fields import BaseSchema, ChoiceField, PageField, QueryField, RefField, StrippedString
from letterboxd import refs

# Choice tables as literals; the publishing tooling reads enums from this file's AST.
SEARCH_TYPES = ["films", "reviews", "lists", "original-lists", "cast-crew", "members", "tags"]
BROWSE_SORTS = ["popular", "popular-week", "popular-month", "popular-year", "rating", "rating-lowest",
                "release", "release-earliest", "name", "longest", "shortest"]
FILM_REVIEW_SORTS = ["popular", "newest", "oldest", "rating", "rating-lowest"]
FILM_LIST_SORTS = ["popular", "newest", "oldest", "name"]
FILM_MEMBER_TYPES = ["watched", "fans", "likes"]
FILM_MEMBER_SORTS = ["newest", "oldest", "name", "popular"]
MEMBER_FILM_SORTS = ["release", "release-earliest", "added", "added-earliest", "rated", "rated-earliest",
                     "member-rating", "member-rating-lowest", "rating", "rating-lowest", "name",
                     "popular", "longest", "shortest"]
WATCHLIST_SORTS = ["added", "added-earliest", "release", "release-earliest", "rating", "rating-lowest",
                   "name", "popular", "longest", "shortest"]
MEMBER_REVIEW_SORTS = ["newest", "oldest", "popular", "member-rating", "member-rating-lowest", "release",
                       "release-earliest", "name"]
MEMBER_LIST_SORTS = ["updated", "newest", "oldest", "popular", "name"]
LIST_ENTRY_SORTS = ["list-order", "reverse", "added", "added-earliest", "owner-rating", "owner-rating-lowest",
                    "rating", "rating-lowest", "release", "release-earliest", "name", "popular",
                    "longest", "shortest"]
PERSON_SORTS = ["popular", "billing", "rating", "rating-lowest", "release", "release-earliest", "name",
                "longest", "shortest"]
TAG_FILM_SORTS = ["popular", "rating", "rating-lowest", "release", "release-earliest", "name",
                  "longest", "shortest"]
PERIODS = ["all-time", "week", "month", "year"]
LIKE_TYPES = ["films", "reviews", "lists"]
TAG_TYPES = ["films", "reviews", "lists"]
MEMBER_TAG_TYPES = ["films", "lists", "reviews"]
AVAILABILITY_FORMATS = ["best", "sd", "hd", "4k"]
POSTER_WIDTHS = [70, 125, 150, 230, 300, 500, 1000]
ROLES = sorted(refs.ROLES)
COMMENT_MAX_PAGE = 50          # comments page p re-downloads p x 20 comments (see comments.py)
FIRST_YEAR = 1870


# ---- id-or-link fields -------------------------------------------------------------

class FilmRefField(RefField):
    resolver = staticmethod(refs.resolve_film)


class MemberRefField(RefField):
    resolver = staticmethod(refs.resolve_member)


class ListRefField(RefField):
    resolver = staticmethod(refs.resolve_list)


class ReviewRefField(RefField):
    resolver = staticmethod(refs.resolve_review)


class PersonRefField(RefField):
    resolver = staticmethod(refs.resolve_person)


class CollectionRefField(RefField):
    resolver = staticmethod(refs.resolve_collection)


class ThemeRefField(RefField):
    resolver = staticmethod(refs.resolve_theme)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


class TagRefField(RefField):
    resolver = staticmethod(refs.resolve_tag)


class CountryField(RefField):
    resolver = staticmethod(refs.country_slug)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


class LanguageField(CountryField):
    resolver = staticmethod(refs.language_slug)


class AvailabilityCountryField(StrippedString):
    """ISO alpha-2 / alpha-3 of a country Letterboxd has availability for;
    returned upper-cased as given (films.availability maps it)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", "US")
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return "US"
        try:
            refs.availability_locale(value)
        except ValueError as e:
            raise ValidationError(str(e))
        return value.upper()


class ServiceField(StrippedString):
    """A streaming-service slug as the site's /on/<service>/ filter uses it
    (e.g. netflix-us, amazon-prime-video-us, mubi)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        slug = value.strip().lower().replace(" ", "-")
        if not refs.SLUG_RE.match(slug):
            raise ValidationError("Must be a service slug such as netflix-us or mubi.")
        return slug


# ---- value fields -----------------------------------------------------------------

class RatingField(fields.Float):
    """Star rating 0.5-5 in half steps (e.g. 3.5)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        if not (0.5 <= value <= 5) or (value * 2) != int(value * 2):
            raise ValidationError("Must be a star rating from 0.5 to 5 in half steps (e.g. 3.5).")
        return float(value)


class GenresField(fields.Field):
    """Comma-separated genres; prefix one with '-' to exclude it
    (horror,-comedy)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = [v.strip() for v in str(value).split(",") if v.strip()]
        if not items:
            return None
        try:
            return refs.genre_segment(items).split("+")
        except ValueError as e:
            raise ValidationError(str(e))


class DecadeField(StrippedString):
    """'2020s' or '2020' -> '2020s' (1870s…2020s)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        raw = value.lower().rstrip("s")
        if not raw.isdigit() or int(raw) % 10 or not (FIRST_YEAR <= int(raw) <= date.today().year):
            raise ValidationError("Must be a decade such as 1990s or 2020s.")
        return f"{int(raw)}s"


class YearField(fields.Integer):
    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        kwargs.setdefault("validate", validate.Range(min=FIRST_YEAR, max=date.today().year + 10))
        super().__init__(strict=False, **kwargs)


class CursorField(StrippedString):
    """Opaque next_cursor from the previous response (absent = first page)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        kwargs.setdefault("validate", validate.Length(max=300))
        super().__init__(**kwargs)


class ActivityCursorField(CursorField):
    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is not None and not value.isdigit():
            raise ValidationError("Pass the next_cursor value from the previous response unchanged.")
        return value


def _choice(values, default):
    return ChoiceField(values, load_default=default)


# ---- schemas ---------------------------------------------------------------------

class EmptySchema(BaseSchema):
    pass


class SearchSchema(BaseSchema):
    query = QueryField()
    type = _choice(SEARCH_TYPES, "films")
    cursor = CursorField()


class AutocompleteSchema(BaseSchema):
    query = QueryField(max_length=100)
    limit = fields.Integer(load_default=10, validate=validate.Range(min=1, max=20), strict=False)


class FilmSchema(BaseSchema):
    film = FilmRefField()


class AvailabilitySchema(FilmSchema):
    country = AvailabilityCountryField()
    format = _choice(AVAILABILITY_FORMATS, "best")


class PosterSchema(FilmSchema):
    width = fields.Integer(load_default=500, validate=validate.OneOf(POSTER_WIDTHS), strict=False)


class FilmReviewsSchema(FilmSchema):
    sort = _choice(FILM_REVIEW_SORTS, "popular")
    rating = RatingField()
    page = PageField(max_page=1000)


class FilmListsSchema(FilmSchema):
    sort = _choice(FILM_LIST_SORTS, "popular")
    page = PageField(max_page=1000)


class FilmMembersSchema(FilmSchema):
    type = _choice(FILM_MEMBER_TYPES, "watched")
    sort = _choice(FILM_MEMBER_SORTS, "newest")
    rating = RatingField()
    page = PageField(max_page=1000)

    @validates_schema
    def _rating_only_for_watched(self, data, **kwargs):
        if data.get("rating") is not None and data.get("type") != "watched":
            raise ValidationError({"rating": ["rating filters type=watched only."]})


class BrowseSchema(BaseSchema):
    sort = _choice(BROWSE_SORTS, "popular")
    genres = GenresField()
    decade = DecadeField()
    year = YearField()
    country = CountryField()
    language = LanguageField()
    service = ServiceField()
    theme = ThemeRefField()
    page = PageField(max_page=1000)

    @validates_schema
    def _decade_or_year(self, data, **kwargs):
        if data.get("decade") and data.get("year"):
            raise ValidationError({"year": ["Use either decade or year, not both."]})


class CollectionSchema(BaseSchema):
    collection = CollectionRefField()
    sort = _choice(BROWSE_SORTS, "release-earliest")
    page = PageField(max_page=100)


class PeriodSchema(BaseSchema):
    period = _choice(PERIODS, "all-time")
    page = PageField(max_page=1000)


class PopularReviewsSchema(BaseSchema):
    period = _choice(PERIODS, "week")
    page = PageField(max_page=1000)


class TagSchema(BaseSchema):
    tag = TagRefField()
    type = _choice(TAG_TYPES, "films")
    sort = _choice(TAG_FILM_SORTS, "popular")
    page = PageField(max_page=1000)


class ListSchema(BaseSchema):
    list = ListRefField()
    sort = _choice(LIST_ENTRY_SORTS, "list-order")
    page = PageField(max_page=100)


class ListCommentsSchema(BaseSchema):
    list = ListRefField()
    page = PageField(max_page=COMMENT_MAX_PAGE)


class ReviewSchema(BaseSchema):
    review = ReviewRefField()


class ReviewCommentsSchema(ReviewSchema):
    page = PageField(max_page=COMMENT_MAX_PAGE)


class MemberSchema(BaseSchema):
    member = MemberRefField()


class MemberFilmsSchema(MemberSchema):
    sort = _choice(MEMBER_FILM_SORTS, "release")
    rating = RatingField()
    genres = GenresField()
    decade = DecadeField()
    year = YearField()
    page = PageField(max_page=1000)

    @validates_schema
    def _decade_or_year(self, data, **kwargs):
        if data.get("decade") and data.get("year"):
            raise ValidationError({"year": ["Use either decade or year, not both."]})


class DiarySchema(MemberSchema):
    year = YearField()
    month = fields.Integer(load_default=None, validate=validate.Range(min=1, max=12), strict=False)
    page = PageField(max_page=1000)

    @validates_schema
    def _month_needs_year(self, data, **kwargs):
        if data.get("month") and not data.get("year"):
            raise ValidationError({"month": ["month needs year."]})


class MemberReviewsSchema(MemberSchema):
    sort = _choice(MEMBER_REVIEW_SORTS, "newest")
    year = YearField()
    page = PageField(max_page=1000)


class WatchlistSchema(MemberSchema):
    sort = _choice(WATCHLIST_SORTS, "added")
    genres = GenresField()
    decade = DecadeField()
    year = YearField()
    page = PageField(max_page=1000)

    @validates_schema
    def _decade_or_year(self, data, **kwargs):
        if data.get("decade") and data.get("year"):
            raise ValidationError({"year": ["Use either decade or year, not both."]})


class MemberListsSchema(MemberSchema):
    sort = _choice(MEMBER_LIST_SORTS, "updated")
    page = PageField(max_page=1000)


class MemberLikesSchema(MemberSchema):
    type = _choice(LIKE_TYPES, "films")
    page = PageField(max_page=1000)


class MemberPageSchema(MemberSchema):
    page = PageField(max_page=1000)


class ActivitySchema(MemberSchema):
    cursor = ActivityCursorField()


class MemberStatsSchema(MemberSchema):
    year = YearField()


class MemberTagsSchema(MemberSchema):
    type = _choice(MEMBER_TAG_TYPES, "films")
    page = PageField(max_page=100)


class PersonSchema(BaseSchema):
    person = PersonRefField()
    role = ChoiceField(ROLES)
    sort = _choice(PERSON_SORTS, "popular")
    genres = GenresField()
