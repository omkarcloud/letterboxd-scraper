"""Letterboxd reference parsing: ONE param per input that auto-detects its
forms (tripadvisor QueryOrLinkField convention — never a sibling `url`/`id`
pair), plus the sort / filter vocabularies every route shares.

Parsing is offline. Each resolver returns a small JSON-able dict (unused
keys None, so validated params stay a stable response-cache key); fetch.py
turns short links and external ids into site paths at request time.

  film        parasite-2019 | https://letterboxd.com/film/parasite-2019/[reviews/…]
              | https://boxd.it/hTha | tt6751668 | imdb:tt6751668
              | https://www.imdb.com/title/tt6751668/ | tmdb:496243
              | https://www.themoviedb.org/movie/496243-parasite
              (bare digits are a slug — "1917" is a real film slug)
  member      dave | https://letterboxd.com/dave/[films/…]
  list        dave/imdb-top-250 | dave/list/imdb-top-250
              | https://letterboxd.com/dave/list/imdb-top-250/[detail/…] | https://boxd.it/7jLA
  review      getout/parasite-2019 | getout/film/parasite-2019[/2]
              | https://letterboxd.com/getout/film/parasite-2019/ | https://boxd.it/MV5qJ
  person      bong-joon-ho | director/bong-joon-ho
              | https://letterboxd.com/director/bong-joon-ho/[by/rating/]
  collection  toy-story-collection | https://letterboxd.com/films/in/toy-story-collection/
  theme       moving-relationship-stories | mini-theme/<slug> | nanogenre/<slug>
              | https://letterboxd.com/films/theme/<slug>/by/best-match/
  tag         horror | https://letterboxd.com/tag/horror/films/
  country     south-korea | South Korea | KR   (geo.COUNTRIES / COUNTRY_BY_ISO)
  language    korean | Korean | ko             (geo.LANGUAGES / LANGUAGE_BY_ISO)

m.letterboxd.com / www.letterboxd.com links are accepted; query strings and
fragments are ignored.
"""
import re
from urllib.parse import quote, unquote, urlparse

from letterboxd import geo

SITE = "https://letterboxd.com"
SHORT_SITE = "https://boxd.it"
_SITE_HOSTS = {"letterboxd.com", "www.letterboxd.com", "m.letterboxd.com"}
_SHORT_HOSTS = {"boxd.it", "www.boxd.it"}
_IMDB_HOSTS = {"imdb.com", "www.imdb.com", "m.imdb.com"}
_TMDB_HOSTS = {"themoviedb.org", "www.themoviedb.org"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")
_IMDB_RE = re.compile(r"^tt\d{5,10}$")
_SHORT_RE = re.compile(r"^[A-Za-z0-9]{2,12}$")
_DIGITS_RE = re.compile(r"^\d{1,9}$")

# ---- people roles -----------------------------------------------------------
# URL segment -> label, as the film Crew tab and the person role menu use
# them (collected from eight films' crew panels, 2026-09-19).
ROLES = {
    "actor": "Actor", "director": "Director", "co-director": "Co-Director",
    "producer": "Producer", "executive-producer": "Executive Producer",
    "writer": "Writer", "original-writer": "Original Writer", "story": "Story",
    "editor": "Editor", "cinematography": "Cinematography",
    "assistant-director": "Assistant Director", "additional-directing": "Additional Directing",
    "additional-photography": "Additional Photography", "camera-operator": "Camera Operator",
    "lighting": "Lighting", "production-design": "Production Design",
    "art-direction": "Art Direction", "set-decoration": "Set Decoration",
    "special-effects": "Special Effects", "visual-effects": "Visual Effects",
    "title-design": "Title Design", "stunts": "Stunts", "choreography": "Choreography",
    "composer": "Composer", "songs": "Songs", "sound": "Sound",
    "costume-design": "Costume Design", "makeup": "Makeup", "hairstyling": "Hairstyling",
    "casting": "Casting", "studio": "Studio",
}

# First path segments that are site sections, never usernames.
RESERVED = set(ROLES) | {
    "film", "films", "list", "lists", "members", "member", "search", "s", "journal", "tag",
    "tags", "reviews", "review", "activity", "settings", "sign-in", "sign-out", "create-account",
    "pro", "patron", "about", "welcome", "apps", "legal", "contact", "countries", "showdown",
    "csi", "esi", "ajax", "imdb", "tmdb", "year-in-review", "gift-guide", "api-beta", "api",
    "user", "static", "hq", "stories", "story", "podcast", "podcasts", "video-store",
    "watchlist", "diary", "likes", "news", "cast", "crew", "jobs", "faq", "help", "viewing",
}

# ---- genres -----------------------------------------------------------------
GENRES = {
    "action": "Action", "adventure": "Adventure", "animation": "Animation", "comedy": "Comedy",
    "crime": "Crime", "documentary": "Documentary", "drama": "Drama", "family": "Family",
    "fantasy": "Fantasy", "history": "History", "horror": "Horror", "music": "Music",
    "mystery": "Mystery", "romance": "Romance", "science-fiction": "Science Fiction",
    "thriller": "Thriller", "tv-movie": "TV Movie", "war": "War", "western": "Western",
}

# ---- sorts: public value -> URL path fragment ("" = the site's default) -------
BROWSE_SORTS = {
    "popular": "popular", "popular-week": "popular/this/week", "popular-month": "popular/this/month",
    "popular-year": "popular/this/year", "rating": "by/rating", "rating-lowest": "by/rating-lowest",
    "release": "by/release", "release-earliest": "by/release-earliest", "name": "by/name",
    "longest": "by/longest", "shortest": "by/shortest",
}
FILM_REVIEW_SORTS = {
    "popular": "by/activity", "newest": "by/added", "oldest": "by/added-earliest",
    "rating": "by/entry-rating", "rating-lowest": "by/entry-rating-lowest",
}
FILM_LIST_SORTS = {"popular": "by/popular", "newest": "by/newest", "oldest": "by/oldest", "name": "by/name"}
FILM_MEMBER_SORTS = {"newest": "", "oldest": "by/date-earliest", "name": "by/name", "popular": "by/popular"}
# watched = everyone who logged it; fans = has it in their four favourites
FILM_MEMBER_KINDS = {"watched": "members", "fans": "fans", "likes": "likes"}
MEMBER_FILM_SORTS = {
    "release": "", "release-earliest": "by/release-earliest",
    "added": "by/date", "added-earliest": "by/date-earliest",
    "rated": "by/rated-date", "rated-earliest": "by/rated-date-earliest",
    "member-rating": "by/entry-rating", "member-rating-lowest": "by/entry-rating-lowest",
    "rating": "by/rating", "rating-lowest": "by/rating-lowest",
    "name": "by/name", "popular": "by/popular", "longest": "by/longest", "shortest": "by/shortest",
}
WATCHLIST_SORTS = {
    "added": "", "added-earliest": "by/date-earliest", "release": "by/release",
    "release-earliest": "by/release-earliest", "rating": "by/rating", "rating-lowest": "by/rating-lowest",
    "name": "by/name", "popular": "by/popular", "longest": "by/longest", "shortest": "by/shortest",
}
MEMBER_REVIEW_SORTS = {
    "newest": "", "oldest": "by/added-earliest", "popular": "by/activity",
    "member-rating": "by/entry-rating", "member-rating-lowest": "by/entry-rating-lowest",
    "release": "by/release", "release-earliest": "by/release-earliest", "name": "by/name",
}
MEMBER_LIST_SORTS = {"updated": "", "newest": "by/newest", "oldest": "by/oldest",
                     "popular": "by/popular", "name": "by/name"}
LIST_ENTRY_SORTS = {
    "list-order": "", "reverse": "by/reverse", "added": "by/added", "added-earliest": "by/added-earliest",
    "owner-rating": "by/owner-rating", "owner-rating-lowest": "by/owner-rating-lowest",
    "rating": "by/rating", "rating-lowest": "by/rating-lowest", "release": "by/release",
    "release-earliest": "by/release-earliest", "name": "by/name", "popular": "by/popular",
    "longest": "by/longest", "shortest": "by/shortest",
}
PERSON_SORTS = {
    "popular": "", "billing": "by/billing", "rating": "by/rating", "rating-lowest": "by/rating-lowest",
    "release": "by/release", "release-earliest": "by/release-earliest", "name": "by/name",
    "longest": "by/longest", "shortest": "by/shortest",
}
TAG_FILM_SORTS = {"popular": "", "rating": "by/rating", "rating-lowest": "by/rating-lowest",
                  "release": "by/release", "release-earliest": "by/release-earliest", "name": "by/name",
                  "longest": "by/longest", "shortest": "by/shortest"}
LIKE_TYPES = ["films", "reviews", "lists"]
TAG_TYPES = ["films", "reviews", "lists"]
PERIODS = {"all-time": "", "week": "this/week", "month": "this/month", "year": "this/year"}
SEARCH_TYPES = ["films", "reviews", "lists", "original-lists", "cast-crew", "members", "tags"]
THEME_KINDS = ("theme", "mini-theme", "nanogenre")
AVAILABILITY_FORMATS = {"best": "best", "sd": "sd", "hd": "hd", "4k": "4k"}


# ---- link parsing -------------------------------------------------------------

def _looks_like_link(text):
    head = text.split("/")[0].lower()
    return text.startswith(("http://", "https://", "//")) or "." in head


def parse_link(text):
    """A link -> (host, [path segments]) with segments URL-decoded and empty
    ones dropped. ValueError when it is not a URL at all."""
    url = text if "://" in text else ("https:" + text if text.startswith("//") else "https://" + text)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("not a link")
    return host, [unquote(s) for s in parsed.path.split("/") if s]


def _site_segments(text, what):
    """letterboxd.com link -> path segments; ValueError for any other site."""
    host, segs = parse_link(text)
    if host not in _SITE_HOSTS:
        raise ValueError(f"Expected a {what} or a letterboxd.com link, got a {host} link.")
    return segs


def _short(text):
    """boxd.it link -> canonical short link, else None."""
    if not _looks_like_link(text):
        return None
    host, segs = parse_link(text)
    if host in _SHORT_HOSTS:
        if len(segs) != 1 or not _SHORT_RE.match(segs[0]):
            raise ValueError("Unrecognised boxd.it short link.")
        return f"{SHORT_SITE}/{segs[0]}"
    return None


def _slug(value, what):
    value = value.strip().strip("/").lower()
    if not SLUG_RE.match(value):
        raise ValueError(f"'{value}' is not a valid Letterboxd {what} slug "
                         f"(lowercase letters, digits and hyphens, e.g. parasite-2019).")
    return value


def username(value):
    value = value.strip().strip("/").lstrip("@")
    if not USERNAME_RE.match(value):
        raise ValueError(f"'{value}' is not a valid Letterboxd username (letters, digits and underscores).")
    if value.lower() in RESERVED:
        raise ValueError(f"'{value}' is a Letterboxd site section, not a member.")
    return value.lower()


# ---- resolvers ------------------------------------------------------------------

def resolve_film(value):
    """-> {"slug", "short_link", "imdb_id", "tmdb_id"} (exactly one set)."""
    out = {"slug": None, "short_link": None, "imdb_id": None, "tmdb_id": None}
    text = value.strip()
    low = text.lower()
    if low.startswith("imdb:"):
        low = low[5:].strip()
    if _IMDB_RE.match(low):
        out["imdb_id"] = low
        return out
    if low.startswith("tmdb:"):
        tid = low[5:].strip()
        if not _DIGITS_RE.match(tid):
            raise ValueError("tmdb: must be followed by a numeric TMDB movie id, e.g. tmdb:496243.")
        out["tmdb_id"] = tid
        return out
    short = _short(text)
    if short:
        out["short_link"] = short
        return out
    if _looks_like_link(text):
        host, segs = parse_link(text)
        if host in _IMDB_HOSTS:
            imdb = next((s for s in segs if _IMDB_RE.match(s.lower())), None)
            if not imdb:
                raise ValueError("IMDb link without a title id (tt…).")
            out["imdb_id"] = imdb.lower()
            return out
        if host in _TMDB_HOSTS:
            if len(segs) >= 2 and segs[0] == "movie" and re.match(r"^\d+", segs[1]):
                out["tmdb_id"] = re.match(r"^\d+", segs[1]).group(0)
                return out
            raise ValueError("Only TMDB movie links (themoviedb.org/movie/<id>) are supported.")
        segs = _site_segments(text, "film slug")
        if "film" in segs:
            i = segs.index("film")
            if i + 1 < len(segs):
                out["slug"] = _slug(segs[i + 1], "film")
                return out
        if len(segs) >= 2 and segs[0] in ("imdb", "tmdb"):
            key = "imdb_id" if segs[0] == "imdb" else "tmdb_id"
            if (_IMDB_RE if key == "imdb_id" else _DIGITS_RE).match(segs[1].lower()):
                out[key] = segs[1].lower()
                return out
        raise ValueError("Not a Letterboxd film link (expected letterboxd.com/film/<slug>/).")
    if text.startswith("film/"):
        text = text[5:]
    out["slug"] = _slug(text, "film")
    return out


def resolve_member(value):
    """-> {"username"} (lowercased, as the site's URLs use it)."""
    text = value.strip()
    if _looks_like_link(text):
        segs = _site_segments(text, "username")
        if not segs:
            raise ValueError("Link has no member in its path.")
        return {"username": username(segs[0])}
    return {"username": username(text)}


def resolve_list(value):
    """-> {"username", "slug", "short_link"}."""
    out = {"username": None, "slug": None, "short_link": None}
    text = value.strip()
    short = _short(text)
    if short:
        out["short_link"] = short
        return out
    if _looks_like_link(text):
        segs = _site_segments(text, "list")
        if len(segs) >= 3 and segs[1] == "list":
            out["username"], out["slug"] = username(segs[0]), _slug(segs[2], "list")
            return out
        raise ValueError("Not a Letterboxd list link (expected letterboxd.com/<member>/list/<slug>/).")
    segs = [s for s in text.split("/") if s]
    if len(segs) == 3 and segs[1] == "list":
        segs = [segs[0], segs[2]]
    if len(segs) != 2:
        raise ValueError("Give a list as <member>/<list-slug> (e.g. dave/imdb-top-250) or its link.")
    out["username"], out["slug"] = username(segs[0]), _slug(segs[1], "list")
    return out


def resolve_review(value):
    """-> {"username", "film_slug", "entry", "short_link"}; entry is the
    1-based rewatch number in /<member>/film/<slug>/<n>/ (None = latest)."""
    out = {"username": None, "film_slug": None, "entry": None, "short_link": None}
    text = value.strip()
    short = _short(text)
    if short:
        out["short_link"] = short
        return out
    if _looks_like_link(text):
        segs = _site_segments(text, "review")
    else:
        segs = [s for s in text.split("/") if s]
        if len(segs) == 2:
            segs = [segs[0], "film", segs[1]]
    if len(segs) >= 3 and segs[1] == "film":
        out["username"], out["film_slug"] = username(segs[0]), _slug(segs[2], "film")
        if len(segs) >= 4 and segs[3].isdigit():
            out["entry"] = int(segs[3])
        return out
    raise ValueError("Give a review as <member>/<film-slug> (e.g. getout/parasite-2019), "
                     "a letterboxd.com/<member>/film/<slug>/ link or a boxd.it link.")


def resolve_person(value):
    """-> {"role" (None = auto-detect), "slug"}."""
    text = value.strip()
    if _looks_like_link(text):
        segs = _site_segments(text, "person slug")
    else:
        segs = [s for s in text.lower().split("/") if s]
    if len(segs) >= 2 and segs[0] in ROLES:
        return {"role": segs[0], "slug": _slug(segs[1], "person")}
    if len(segs) == 1 and not _looks_like_link(text):
        return {"role": None, "slug": _slug(segs[0], "person")}
    raise ValueError("Give a person as a slug (bong-joon-ho), <role>/<slug> (director/bong-joon-ho) "
                     "or a letterboxd.com/<role>/<slug>/ link.")


def resolve_collection(value):
    text = value.strip()
    if _looks_like_link(text):
        segs = _site_segments(text, "collection")
        if len(segs) >= 3 and segs[0] == "films" and segs[1] == "in":
            return _slug(segs[2], "collection")
        raise ValueError("Not a collection link (expected letterboxd.com/films/in/<slug>/).")
    return _slug(text, "collection")


def resolve_theme(value):
    """-> {"kind": theme | mini-theme | nanogenre, "slug"}."""
    text = value.strip()
    if _looks_like_link(text):
        segs = _site_segments(text, "theme")
        segs = segs[1:] if segs and segs[0] == "films" else segs
    else:
        segs = [s for s in text.lower().split("/") if s]
    if len(segs) >= 2 and segs[0] in THEME_KINDS:
        return {"kind": segs[0], "slug": _slug(segs[1], "theme")}
    if len(segs) == 1 and not _looks_like_link(text):
        return {"kind": "theme", "slug": _slug(segs[0], "theme")}
    raise ValueError("Give a theme slug, mini-theme/<slug>, nanogenre/<slug> or a "
                     "letterboxd.com/films/theme/<slug>/ link (films/details lists a film's themes).")


def resolve_tag(value):
    """Tag text or a /tag/<slug>/ link -> the tag's URL slug."""
    text = value.strip()
    if _looks_like_link(text):
        segs = _site_segments(text, "tag")
        if "tag" in segs:
            i = segs.index("tag")
            if i + 1 < len(segs):
                return segs[i + 1].lower()
        raise ValueError("Not a tag link (expected letterboxd.com/tag/<tag>/…).")
    slug = re.sub(r"[\s/?#%&+]+", "-", text.lower()).strip("-")
    if not slug:
        raise ValueError("Empty tag.")
    return slug


def country_slug(value):
    text = value.strip()
    key = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if key in geo.COUNTRIES:
        return key
    if len(text) == 2 and text.upper() in geo.COUNTRY_BY_ISO:
        return geo.COUNTRY_BY_ISO[text.upper()]
    for slug, name in geo.COUNTRIES.items():
        if name.lower() == text.lower():
            return slug
    raise ValueError(f"Unknown country '{text}': use a name (South Korea), a Letterboxd slug "
                     f"(south-korea) or an ISO code (KR).")


def language_slug(value):
    text = value.strip()
    key = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if key in geo.LANGUAGES:
        return key
    if len(text) == 2 and text.lower() in geo.LANGUAGE_BY_ISO:
        return geo.LANGUAGE_BY_ISO[text.lower()]
    for slug, name in geo.LANGUAGES.items():
        if name.replace("\xa0", " ").lower() == text.lower() or key == re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"):
            return slug
    raise ValueError(f"Unknown language '{text}': use a name (Korean), a Letterboxd slug "
                     f"(korean) or an ISO 639-1 code (ko).")


def availability_locale(value):
    """ISO alpha-2 or alpha-3 -> the alpha-3 locale Letterboxd's JustWatch
    feed takes."""
    code = value.strip().upper()
    if code == "UK":
        code = "GB"
    if len(code) == 2 and code in geo.AVAILABILITY_COUNTRIES:
        return geo.AVAILABILITY_COUNTRIES[code]
    if len(code) == 3 and code in geo.AVAILABILITY_COUNTRIES.values():
        return code
    raise ValueError(f"'{value}' is not a country Letterboxd has streaming availability for "
                     f"(ISO code such as US, GB, IN or USA).")


def genre_segment(values):
    """['horror', '-comedy'] -> 'horror+-comedy' (a leading '-' excludes)."""
    parts = []
    for raw in values:
        exclude = raw.startswith("-")
        name = raw.lstrip("-").strip().lower().replace(" ", "-")
        if name not in GENRES:
            match = next((k for k, v in GENRES.items() if v.lower() == name.replace("-", " ")), None)
            if not match:
                raise ValueError(f"Unknown genre '{raw}'. Use: {', '.join(GENRES)} "
                                 f"(prefix with '-' to exclude).")
            name = match
        token = ("-" if exclude else "") + name
        if token not in parts:
            parts.append(token)
    return "+".join(parts)


def quote_segment(value):
    """A URL path segment with unicode / reserved characters escaped."""
    return quote(value, safe="-_.~+")
