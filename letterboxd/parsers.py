"""Letterboxd normalizers: server-rendered HTML / fragments / small JSON
payloads -> one clean snake_case shape per resource (film, review, member,
list, person, comment, …). Pure functions — no network; endpoint modules
fetch, call these, then attach posters (shared.with_posters).

Conventions: `link` for web URLs (absolute), `short_link` for boxd.it,
`rating` in stars (0.5-5.0, half-star steps), `is_*` / `has_*` booleans,
ISO dates (YYYY-MM-DD) and UTC datetimes (YYYY-MM-DDTHH:MM:SSZ), counters as
numbers, null for missing (never "" or omitted keys).

Upstream data dropped on purpose (and why):
  * data-likeable-identifier / data-postered-identifier `typeName`, the
    `lid` inside identifiers (kept only as `short_link`), csrf tokens,
    report / edit / delete / follow action URLs, empty-poster placeholder
    images, poster picker / backdrop picker URLs — UI plumbing.
  * JustWatch `viewUrl` click-tracking wrapper (the destination in its `r=`
    param is returned as `link`, the `cx` payload's price/currency as numbers).
  * "your-rating" / "your-interest" sorts and friend panels — they need a
    logged-in session.
  * Abbreviated counters ("431K") are expanded to numbers; they are
    approximate where the site only shows the abbreviation (list likes and
    comments on list cards) — exact counts come from the detail endpoints.
"""
import base64
import html as html_lib
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from letterboxd import refs

SITE = refs.SITE
_YEAR_SUFFIX_RE = re.compile(r"^(.*?)\s*\((\d{4})\)$")
_COUNT_RE = re.compile(r"([\d.,]+)\s*([KkMm]?)")
_RATED_RE = re.compile(r"rated-(?:large-)?(\d{1,2})\b")
_AVATAR_SIZE_RE = re.compile(r"-0-\d+-0-\d+-crop")
_DEFAULT_AVATAR_RE = re.compile(r"/static/img/avatar\d*|/static/img/avatar-actor|/static/img/empty")
_UID_RE = re.compile(r"(film|viewing|filmList|list):(\d+)")


# ---- value helpers -------------------------------------------------------------
# (kept local rather than imported from another site's package so this module
# stands alone — the open-source kit ships it verbatim)

def to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def text(value):
    """Trimmed string or None ('' / whitespace -> None)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def iso_datetime(value):
    """'2023-05-17T14:41:20.302Z' -> '2023-05-17T14:41:20Z' (UTC); junk -> None."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def soup(markup):
    return BeautifulSoup(markup or "", "lxml")


def clean(node):
    """A node / string -> whitespace-collapsed text or None."""
    if node is None:
        return None
    value = node.get_text() if hasattr(node, "get_text") else str(node)
    value = " ".join(html_lib.unescape(value).replace("\xa0", " ").split())
    return value or None


def absolute(href):
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return SITE + href
    return href


def count(value):
    """'1,234' -> 1234; '13.8K' -> 13800; '2.9M' -> 2900000; junk -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = _COUNT_RE.search(str(value).replace("\xa0", " "))
    if not m:
        return None
    number, suffix = m.group(1).replace(",", ""), m.group(2).lower()
    try:
        amount = float(number)
    except ValueError:
        return None
    if suffix == "k":
        amount *= 1000
    elif suffix == "m":
        amount *= 1_000_000
    return int(round(amount))


def to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def stars(label):
    """'★★★½' -> 3.5; '½' -> 0.5; 'half-★' -> 0.5; junk -> None."""
    if not label:
        return None
    label = str(label).strip()
    if label.startswith("half"):
        return 0.5
    full = label.count("★")
    half = 0.5 if "½" in label else 0.0
    value = full + half
    return value if value else None


def rating_from_ten(value):
    """Letterboxd's internal 1-10 scale -> stars (8 -> 4.0); 0/None -> None."""
    number = to_int(value)
    return number / 2 if number else None


def rating_from_class(node):
    """A node whose class carries rated-N (or containing one) -> stars."""
    if node is None:
        return None
    classes = " ".join(node.get("class") or []) if hasattr(node, "get") else str(node)
    m = _RATED_RE.search(classes)
    if not m and hasattr(node, "select_one"):
        inner = node.select_one("[class*='rated-']")
        m = _RATED_RE.search(" ".join(inner.get("class") or [])) if inner else None
    return rating_from_ten(m.group(1)) if m else None


def svg_rating(node):
    """svg.-rating aria-label stars inside `node` -> stars."""
    if node is None:
        return None
    svg = node.select_one("svg.-rating[aria-label], .inline-rating svg[aria-label]")
    return stars(svg.get("aria-label")) if svg else None


def has_liked_icon(node):
    if node is None:
        return False
    return bool(node.select_one("svg[aria-label='Liked'], svg.inline-liked, .icon-liked"))


def iso_date(value):
    """'2019-08-09' / '2019-08-09T…' / '21 May 2019' / '14 Aug 2019' -> 'YYYY-MM-DD'."""
    if not value:
        return None
    value = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%A %B %d, %Y", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def rfc822_datetime(value):
    """RSS pubDate ('Sat, 19 Sep 2026 10:53:14 +1200') -> UTC ISO datetime."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value.strip(), "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_datetime(value):
    """Epoch milliseconds -> UTC ISO datetime."""
    number = to_int(value)
    if not number:
        return None
    return datetime.fromtimestamp(number / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def name_and_year(display):
    """'Parasite (2019)' -> ('Parasite', 2019); 'Untitled' -> ('Untitled', None)."""
    display = text(html_lib.unescape(display or "")) or None
    if not display:
        return None, None
    m = _YEAR_SUFFIX_RE.match(display)
    if m:
        return m.group(1).strip() or display, int(m.group(2))
    return display, None


def avatar(url):
    """Member avatar -> 1000px link; the site's default silhouette -> None."""
    if not url or _DEFAULT_AVATAR_RE.search(url):
        return None
    url = absolute(url)
    if "gravatar.com" in url:
        url = re.sub(r"([?&])size=\d+", r"\1size=1000", url)
        return url
    return _AVATAR_SIZE_RE.sub("-0-1000-0-1000-crop", url)


def json_attr(node, name):
    raw = node.get(name) if node is not None else None
    if not raw:
        return {}
    try:
        value = json.loads(html_lib.unescape(raw))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def short_link(lid):
    return f"https://boxd.it/{lid}" if lid else None


def path_segments(href):
    return [s for s in urlparse(href or "").path.split("/") if s]


def detached(node):
    """A copy of `node` (html_text mutates <br>s) — the element itself, not
    the <html><body> wrapper lxml adds."""
    if node is None:
        return None
    body = BeautifulSoup(str(node), "lxml").body
    return body.find(True) if body is not None else None


def html_text(node):
    """Rich text node -> (plain text with paragraph breaks, inner HTML)."""
    if node is None:
        return None, None
    for br in node.find_all("br"):
        br.replace_with("\n")
    paragraphs = [clean(p) for p in node.find_all("p")] or [clean(node)]
    plain = "\n\n".join(p for p in paragraphs if p) or None
    inner = "".join(str(c) for c in node.contents).strip() or None
    return plain, inner


def pages_total(doc):
    """Highest page number in a .paginate-pages block (None when absent)."""
    numbers = [to_int(clean(a)) for a in doc.select(".paginate-pages li a, .paginate-pages li span")]
    numbers = [n for n in numbers if n]
    return max(numbers) if numbers else None


def has_next(doc):
    # diary / year-filtered pages also carry a "Next Month" / next-year
    # a.next (e.g. /dave/diary/for/2024/4/) — only a /page/N/ link is a page
    return any("/page/" in (a.get("href") or "")
               for a in doc.select(".pagination a.next, .paginate-nextprev a.next"))


def pagination(page, per_page, has_more, total_count=None, total_pages=None):
    """The block route_glue.paginate() lifts into the flat gateway shape."""
    if total_pages is None:
        if total_count is not None and per_page:
            total_pages = (int(total_count) + per_page - 1) // per_page
        else:
            total_pages = page + 1 if has_more else page
    return {"page": page, "items_per_page": per_page,
            "total_pages": max(total_pages, page + 1 if has_more else page if page == 1 else total_pages),
            "total_count": total_count}


def ordered(d, keys):
    """dict with `keys` first in that order, then the rest."""
    out = {k: d.get(k) for k in keys}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


# ---- films -----------------------------------------------------------------------

def poster_request(node):
    """LazyPoster component -> (uid, base link, cache key) for fetch.posters."""
    ident = json_attr(node, "data-postered-identifier")
    path = json_attr(node, "data-resolvable-poster-path")
    uid = ident.get("uid") or (path.get("postered") or {}).get("uid")
    base = path.get("posteredBaseLink") or node.get("data-item-link")
    if not path.get("hasDefaultPoster", True) and not path.get("fallback"):
        return (uid, None, None)
    return (uid, base, path.get("cacheBustingKey"))


def film_from_poster(node):
    """A LazyPoster react-component div -> film card (poster filled later)."""
    if node is None:
        return None
    ident = json_attr(node, "data-postered-identifier")
    name, year = name_and_year(node.get("data-item-full-display-name") or node.get("data-item-name"))
    slug = node.get("data-item-slug")
    link = node.get("data-item-link") or (f"/film/{slug}/" if slug else None)
    if not (name or slug):
        return None
    uid = ident.get("uid") or ""
    film_id = to_int(uid.split(":")[-1]) if uid.startswith("film:") else None
    return {
        "id": film_id,
        "slug": slug if slug and not slug.startswith("film:") else (path_segments(link)[-1] if link else None),
        "name": name,
        "year": year,
        "link": absolute(link),
        "short_link": short_link(ident.get("lid")),
        "poster": None,
        "_poster": poster_request(node),
    }


def poster_nodes(root):
    return root.select("div.react-component[data-component-class='LazyPoster']") if root else []


def film_cards(root, container_selector=None):
    """Every LazyPoster in `root` -> (film card, its container node)."""
    out = []
    for node in poster_nodes(root):
        film = film_from_poster(node)
        if film:
            container = node.find_parent(container_selector) if container_selector else node.parent
            out.append((film, container))
    return out


def grid_film(node):
    """A grid item (li.posteritem / li.griditem / .poster-container) ->
    film card + the per-item extras the grid carries."""
    poster = node.select_one("div.react-component[data-component-class='LazyPoster']")
    film = film_from_poster(poster)
    if not film:
        return None
    avg = to_float(node.get("data-average-rating"))
    if avg is not None:
        film["average_rating"] = avg
    owner = node.get("data-owner-rating")
    if owner is not None:
        film["owner_rating"] = rating_from_ten(owner)
    viewing = node.select_one(".poster-viewingdata")
    if viewing is not None:
        film["member_rating"] = rating_from_class(viewing.select_one(".rating"))
        film["is_liked"] = bool(viewing.select_one(".like, .icon-liked"))
    number = node.select_one(".list-number")
    if number is not None:
        film["position"] = to_int(clean(number))
    return film


def grid_films(doc, selector="li.posteritem, li.griditem"):
    items = []
    for node in doc.select(selector):
        film = grid_film(node)
        if film:
            items.append(film)
    return items


def browse_total(doc):
    """'There are 17,002 films in Korean.' -> 17002."""
    heading = doc.select_one(".ui-block-heading")
    if heading is None:
        return None
    m = re.search(r"There (?:are|is) ([\d,]+) film", clean(heading) or "")
    return count(m.group(1)) if m else None


def film_json(data):
    """/film/<slug>/json/ -> compact film card."""
    if not isinstance(data, dict) or not data.get("slug"):
        return None
    directors = [clean(d.get("name")) for d in data.get("directors") or [] if isinstance(d, dict)]
    return {
        "id": to_int(data.get("id")),
        "slug": data.get("slug"),
        "name": text(data.get("name")),
        "original_name": text(data.get("originalName")),
        "year": to_int(data.get("releaseYear")),
        "link": absolute(data.get("url")),
        "short_link": short_link(data.get("lid")),
        "runtime_minutes": to_int(data.get("runTime")),
        "directors": [d for d in directors if d],
    }


def _slug_links(container):
    """a.text-slug links in a block -> [{name, slug, link}]."""
    out = []
    if container is None:
        return out
    for a in container.select("a"):
        href = a.get("href") or ""
        if "/themes/" in href and not re.search(r"/(theme|mini-theme|nanogenre)/", href):
            continue            # the "Show All…" link
        segs = path_segments(href)
        out.append({"name": clean(a), "slug": segs[-1] if segs else None, "link": absolute(href)})
    return out


def _tab_sections(panel):
    """A tab panel of <h3>label</h3><div>links</div> pairs -> {label: div}."""
    sections = {}
    if panel is None:
        return sections
    for h3 in panel.find_all("h3"):
        label = clean(h3.select_one(".crewrole.-full") or h3)
        block = h3.find_next_sibling("div")
        if label and block is not None:
            sections[label] = block
    return sections


def _people(block):
    people = []
    for a in (block.select("a[href]") if block is not None else []):
        segs = path_segments(a.get("href"))
        if len(segs) >= 2:
            people.append({"name": clean(a), "slug": segs[1], "link": absolute(a.get("href"))})
    return people


def film_cast(doc):
    out = []
    for a in doc.select("#tab-panel-cast .cast-list a[href^='/actor/']"):
        segs = path_segments(a.get("href"))
        out.append({
            "name": clean(a),
            "slug": segs[1] if len(segs) > 1 else None,
            "character": text(html_lib.unescape(a.get("title") or "")) or None,
            "link": absolute(a.get("href")),
        })
    return out


def film_crew(doc):
    crew = []
    for label, block in _tab_sections(doc.select_one("#tab-panel-crew")).items():
        people = _people(block)
        role = path_segments(people[0]["link"])[0] if people else None
        crew.append({"role": role, "label": label, "people": people})
    return crew


def film_details_tab(doc):
    sections = _tab_sections(doc.select_one("#tab-panel-details"))
    out = {"studios": [], "countries": [], "primary_language": None, "spoken_languages": [],
           "alternative_titles": []}
    for label, block in sections.items():
        low = label.lower()
        if low.startswith("studio"):
            out["studios"] = _slug_links(block)
        elif low.startswith("countr"):
            out["countries"] = _slug_links(block)
        elif low.startswith("primary language"):
            langs = _slug_links(block)
            out["primary_language"] = langs[0] if langs else None
        elif low.startswith("language") or low.startswith("spoken language"):
            out["spoken_languages"] = _slug_links(block)
        elif low.startswith("alternative title"):
            raw = clean(block) or ""
            out["alternative_titles"] = [t.strip() for t in raw.split(",") if t.strip()]
    return out


def film_genres_tab(doc):
    sections = _tab_sections(doc.select_one("#tab-panel-genres"))
    genres, themes = [], []
    for label, block in sections.items():
        if label.lower().startswith("genre"):
            genres = _slug_links(block)
        elif label.lower().startswith("theme"):
            for item in _slug_links(block):
                segs = path_segments(item["link"])
                kind = next((s for s in segs if s in refs.THEME_KINDS), "theme")
                idx = segs.index(kind) if kind in segs else -1
                slug = segs[idx + 1] if 0 <= idx < len(segs) - 1 else item["slug"]
                themes.append({"name": item["name"], "type": kind, "slug": slug,
                               "link": absolute(f"/films/{kind}/{slug}/")})
    return genres, themes


def film_releases(doc):
    """Releases tab (by date) -> [{type, date, country, certification, note}]."""
    panel = doc.select_one("#tab-panel-releases-by-date") or doc.select_one("#tab-panel-releases")
    releases = []
    if panel is None:
        return releases
    for title in panel.select("h3.release-table-title"):
        kind = clean(title)
        table = title.find_next_sibling("div")
        if table is None:
            continue
        for row in table.select(":scope > .listitem"):
            date = iso_date(clean(row.select_one(".date")))
            for country in row.select(".release-country"):
                releases.append({
                    "type": kind.lower() if kind else None,
                    "date": date,
                    "country": clean(country.select_one(".name")),
                    "certification": clean(country.select_one(".release-certification-badge")),
                    "note": clean(country.select_one(".release-note")),
                })
    return releases


def ld_json(doc):
    node = doc.find("script", type="application/ld+json")
    if node is None or not node.string:
        return {}
    raw = re.sub(r"/\*.*?\*/", "", node.string, flags=re.S).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def iso_duration_minutes(value):
    """'PT2H13M' -> 133."""
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?", value or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def film_page(markup):
    """/film/<slug>/ -> the film details (stats / histogram / poster are
    merged in by films.details from their own calls)."""
    doc = soup(markup)
    ld = ld_json(doc)
    body = doc.body or doc
    header = doc.select_one("section.production-masthead .details") or doc
    backdrop = doc.select_one("#backdrop")
    footer = doc.select_one("p.text-footer")
    uid = backdrop.get("data-production-uid") if backdrop is not None else None
    uid = uid or (doc.select_one("[data-production-uid]") or {}).get("data-production-uid") \
        if doc.select_one("[data-production-uid]") is not None else uid
    canonical = (doc.find("meta", property="og:url") or {}).get("content") or ld.get("url")
    slug = path_segments(canonical)[-1] if canonical else None
    lid = None
    for a in doc.select("a[href*='boxd.it/'], [data-share-url*='boxd.it/']"):
        m = re.search(r"boxd\.it/([A-Za-z0-9]+)", a.get("href") or a.get("data-share-url") or "")
        if m:
            lid = m.group(1)
            break
    if not lid:
        m = re.search(r"https://boxd\.it/([A-Za-z0-9]+)", markup or "")
        lid = m.group(1) if m else None

    synopsis_node = doc.select_one("section.production-synopsis .truncate") or \
        doc.select_one(".production-synopsis .review")
    synopsis = clean(synopsis_node)
    if not synopsis:
        meta = doc.find("meta", attrs={"name": "description"})
        synopsis = clean(meta.get("content")) if meta else None
    genres, themes = film_genres_tab(doc)
    details = film_details_tab(doc)

    imdb = tmdb = None
    for a in (footer.select("a[href]") if footer is not None else []):
        href = a.get("href") or ""
        m = re.search(r"imdb\.com/title/(tt\d+)", href)
        if m:
            imdb = {"id": m.group(1), "link": f"https://www.imdb.com/title/{m.group(1)}/"}
        m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", href)
        if m:
            tmdb = {"id": int(m.group(2)), "type": m.group(1), "link": f"https://www.themoviedb.org/{m.group(1)}/{m.group(2)}/"}
    if tmdb is None and body.get("data-tmdb-id"):
        kind = body.get("data-tmdb-type") or "movie"
        tmdb = {"id": to_int(body.get("data-tmdb-id")), "type": kind,
                "link": f"https://www.themoviedb.org/{kind}/{body.get('data-tmdb-id')}/"}
    runtime = None
    if footer is not None:
        m = re.search(r"(\d+)\s*mins?", clean(footer) or "")
        runtime = int(m.group(1)) if m else None
    runtime = runtime or iso_duration_minutes(ld.get("duration"))

    trailer = None
    t = doc.select_one(".trailer-link a[href], a.js-video-zoom[href*='youtube']")
    if t is not None:
        href = absolute(t.get("href"))
        m = re.search(r"youtube\.com/embed/([A-Za-z0-9_-]{6,})", href or "")
        trailer = {"youtube_id": m.group(1), "link": f"https://www.youtube.com/watch?v={m.group(1)}"} if m \
            else {"youtube_id": None, "link": href}

    # the page links the franchise as "Related Films"; its name comes from
    # /letterboxd/films/collection
    collection = None
    col = doc.select_one("a[href^='/films/in/']")
    if col is not None:
        segs = path_segments(col.get("href"))
        if len(segs) >= 3:
            collection = {"slug": segs[2], "link": absolute(f"/films/in/{segs[2]}/")}

    agg = ld.get("aggregateRating") or {}
    year_node = header.select_one(".releasedate a") if header is not None else None
    original = header.select_one(".originalname") if header is not None else None
    similar = [f for f, _ in film_cards(doc.select_one("section.related-films"))]
    mentions = []
    for a in doc.select("#film-hq-mentions a[href]"):
        label = text(html_lib.unescape(a.get("title") or "")) or clean(a.select_one(".story")) or ""
        kind, _, author = label.partition(" by ")
        segs = path_segments(a.get("href"))
        mentions.append({"type": kind.strip().lower() or None, "author": author.strip() or None,
                         "author_username": segs[0] if segs else None, "link": absolute(a.get("href"))})

    return {
        "id": to_int(uid.split(":")[-1]) if uid and uid.startswith("film:") else None,
        "slug": slug,
        "name": clean(header.select_one(".primaryname .name, h1.primaryname")) if header is not None else text(ld.get("name")),
        "original_name": clean(original),
        "year": to_int(clean(year_node)),
        "link": absolute(canonical) if canonical else (f"{SITE}/film/{slug}/" if slug else None),
        "short_link": short_link(lid),
        "tagline": clean(doc.select_one("section.production-synopsis .tagline")),
        "synopsis": synopsis,
        "runtime_minutes": runtime,
        "release_date": iso_date(ld.get("dateCreated")),
        "directors": _people(header.select_one(".contributorlist")) if header is not None else [],
        "genres": [{"name": g["name"], "slug": g["slug"], "link": g["link"]} for g in genres],
        "themes": themes,
        "cast": film_cast(doc),
        "crew": film_crew(doc),
        "studios": details["studios"],
        "countries": details["countries"],
        "primary_language": details["primary_language"],
        "spoken_languages": details["spoken_languages"],
        "alternative_titles": details["alternative_titles"],
        "releases": film_releases(doc),
        "collection": collection,
        "rating": {
            "average": to_float(agg.get("ratingValue")),
            "count": to_int(agg.get("ratingCount")),
        },
        "reviews_count": to_int(agg.get("reviewCount")),
        "poster": None,
        "backdrop": {
            "link": backdrop.get("data-backdrop2x") or backdrop.get("data-backdrop"),
            "small_link": backdrop.get("data-backdrop-mobile"),
        } if backdrop is not None and backdrop.get("data-backdrop") else None,
        "trailer": trailer,
        "external_ids": {"imdb": imdb, "tmdb": tmdb},
        "similar_films": similar,
        "mentioned_by": mentions,
        "_ld_image": ld.get("image"),
    }


def film_stats(markup):
    """/csi/film/<slug>/stats/ -> {watches, lists, likes, top_500_rank}."""
    doc = soup(markup)

    def exact(cls):
        node = doc.select_one(f".production-statistic.{cls}")
        if node is None:
            return None
        label = node.get("aria-label") or (node.select_one("a") or {}).get("title") if node.select_one("a") else node.get("aria-label")
        m = re.search(r"([\d,]+)", html_lib.unescape(label or ""))
        return count(m.group(1)) if m else count(clean(node))
    return {
        "watches": exact("-watches"),
        "lists": exact("-lists"),
        "likes": exact("-likes"),
        "top_500_rank": exact("-topFilms"),
    }


def histogram(markup_or_node):
    """A rating-histogram block -> {average, count, distribution[10]}."""
    node = soup(markup_or_node) if isinstance(markup_or_node, str) else markup_or_node
    block = node.select_one(".rating-histogram") if node is not None else None
    if block is None:
        return None
    bins = []
    for i, row in enumerate(block.select("tr.column")):
        a = row.select_one("a[title]")
        title = html_lib.unescape(a.get("title")) if a is not None else ""
        m = re.match(r"\s*([\d,]+)\s", title)
        pct = re.search(r"\((\d+)%\)", title)
        bins.append({"rating": (i + 1) / 2, "count": count(m.group(1)) if m else 0,
                     "percent": to_int(pct.group(1)) if pct else None})
    avg_node = block.select_one(".averagerating")
    avg = total = None
    if avg_node is not None:
        title = html_lib.unescape(avg_node.get("title") or "")
        m = re.search(r"average of ([\d.]+) based on ([\d,]+)", title)
        if m:
            avg, total = to_float(m.group(1)), count(m.group(2))
        else:
            avg = to_float(clean(avg_node))
    if total is None and bins:
        total = sum(b["count"] or 0 for b in bins)
    if avg is None and bins:
        weighted = sum((b["count"] or 0) for b in bins)
        if weighted:
            avg = round(sum(b["rating"] * (b["count"] or 0) for b in bins) / weighted, 2)
    return {"average": avg, "count": total, "distribution": bins}


# ---- members -----------------------------------------------------------------------

def badge(node):
    b = node.select_one(".badge") if node is not None else None
    if b is None:
        return None
    for cls in b.get("class") or []:
        if cls.startswith("-") and cls not in ("-small", "-large"):
            return cls[1:]
    return (clean(b) or "").lower() or None


def member_from_link(a, name=None, img=None):
    """An <a href="/<username>/"> (+ avatar img) -> member card."""
    if a is None:
        return None
    segs = path_segments(a.get("href"))
    if not segs:
        return None
    username = segs[0]
    img = img if img is not None else a.select_one("img")
    return {
        "username": username,
        "name": name or (clean(img.get("alt")) if img is not None else None) or clean(a) or username,
        "link": f"{SITE}/{username}/",
        "avatar": avatar(img.get("src")) if img is not None else None,
    }


def person_summary(node):
    """.person-summary / table row member cell -> member card (+ badge)."""
    if node is None:
        return None
    link = node.select_one("a.name[href]") or node.select_one("a.avatar[href]") or node.select_one("a[href]")
    img = node.select_one("a.avatar img, img")
    name_node = node.select_one("a.name")
    name = None
    if name_node is not None:
        name_node = BeautifulSoup(str(name_node), "lxml")
        for b in name_node.select(".badge"):
            b.decompose()
        name = clean(name_node)
    member = member_from_link(link, name=name, img=img)
    if member is not None:
        member["badge"] = badge(node)
    return member


def member_rows(doc):
    """people tables (followers, following, popular members, film members /
    fans / likes) -> member cards with the row's counters."""
    out = []
    for row in doc.select("table.person-table tbody tr, table.member-table tbody tr"):
        cell = row.select_one("td.col-member") or row
        member = person_summary(cell)
        if not member:
            continue
        meta = cell.select_one("small.metadata")
        for a in (meta.select("a") if meta is not None else []):
            label = clean(a) or ""
            if "follower" in label:
                member["followers_count"] = count(label)
            elif "following" in label:
                member["following_count"] = count(label)
            elif "review" in label:
                member["reviews_count"] = count(label)
        watched = row.select_one("td.col-watched a")
        if watched is not None:
            member["films_count"] = count(clean(watched))
        lists = row.select_one("td.col-lists a")
        if lists is not None:
            member["lists_count"] = count(clean(lists))
        likes = row.select_one("td.col-likes a")
        if likes is not None:
            member["liked_films_count"] = count(clean(likes))
        rating_cell = row.select_one("td.col-rating")
        if rating_cell is not None:
            member["rating"] = rating_from_class(rating_cell.select_one(".rating"))
        like_cell = row.select_one("td.col-like")
        if like_cell is not None:
            member["is_liked"] = bool(like_cell.select_one(".icon-liked"))
        review_cell = row.select_one("td.col-review")
        if review_cell is not None:
            review = review_cell.select_one("a[href]")
            member["review_link"] = absolute(review.get("href")) if review is not None else None
        out.append(member)
    return out


def profile(markup, username):
    """/<member>/ -> profile."""
    doc = soup(markup)
    header = doc.select_one(".profile-header") or doc
    name = clean(header.select_one(".person-display-name .displayname .label, .person-display-name .displayname"))
    img = header.select_one(".profile-avatar img")
    stats = {}
    for h4 in header.select(".profile-statistic"):
        label = (clean(h4.select_one(".definition")) or "").lower()
        value = count(clean(h4.select_one(".value")))
        key = {"films": "films", "this year": "films_this_year", "lists": "lists",
               "following": "following", "followers": "followers"}.get(label)
        if key:
            stats[key] = value
    location = website = None
    socials = []
    for item in header.select(".profile-metadata .metadatum"):
        href = item.get("href")
        label = clean(item)
        if not href:
            location = label
            continue
        host = (urlparse(href).hostname or "").lower()
        network = next((n for n in ("twitter", "x.com", "instagram", "bsky", "tiktok", "youtube", "facebook",
                                    "threads", "mastodon", "letterboxd") if n in host), None)
        if network:
            socials.append({"network": "twitter" if network == "x.com" else network, "link": href})
        else:
            website = href
    bio_node = doc.select_one(".profile-person-bio .collapsible-text, .profile-person-bio .body-text")
    bio_full = bio_node.get("data-full-text-url") if bio_node is not None and bio_node.select_one(".collapsed-text") else None
    if bio_node is not None:
        bio_node = bio_node.select_one(".collapsed-text") or bio_node
    bio_text, bio_html = html_text(detached(bio_node)) if bio_node is not None else (None, None)
    favourites = [film for film, _ in film_cards(doc.select_one("#favourites"))]
    watchlist = doc.select_one("section.watchlist-aside")
    watchlist_count = count(clean(watchlist.select_one("a.all-link"))) if watchlist is not None else None
    ratings = doc.select_one("section.ratings-histogram-chart")
    hist = histogram(ratings) if ratings is not None else None
    if hist is not None and ratings is not None:
        hist["count"] = count(clean(ratings.select_one(".section-accessories a"))) or hist["count"]
    member_id = None
    m = re.search(r"person:(\d+)", markup or "")
    if m:
        member_id = int(m.group(1))
    return {
        "id": member_id,
        "username": username,
        "name": name or username,
        "link": f"{SITE}/{username}/",
        "avatar": avatar(img.get("src")) if img is not None else None,
        "badge": badge(header.select_one(".person-display-name")),
        "bio": bio_text,
        "bio_html": bio_html,
        "location": location,
        "website": website,
        "social_links": socials,
        "stats": {
            "films": stats.get("films"),
            "films_this_year": stats.get("films_this_year"),
            "lists": stats.get("lists"),
            "following": stats.get("following"),
            "followers": stats.get("followers"),
            "watchlist": watchlist_count,
        },
        "favorite_films": favourites,
        "ratings": hist,
        "recent_activity": [f for f in (grid_film(li) for li in doc.select("#recent-activity li.griditem")) if f],
        "_bio_full_text": bio_full,
    }


# ---- reviews -----------------------------------------------------------------------

def _viewing_id(node):
    for attr in ("data-object-id", "data-viewing-id"):
        m = _UID_RE.search(node.get(attr) or "") if node.get(attr) else None
        if m and m.group(1) == "viewing":
            return int(m.group(2))
        if attr == "data-viewing-id" and to_int(node.get(attr)):
            return to_int(node.get(attr))
    m = re.search(r"viewing:(\d+)", str(node))
    return int(m.group(1)) if m else None


def review_item(node, film=None):
    """article.production-viewing -> review card. `film` fills the film when
    the page is the film's own reviews list (no poster per item)."""
    if node is None:
        return None
    viewing_id = _viewing_id(node)
    context = node.select_one("a.context[href]")
    title_link = node.select_one("h2.primaryname a[href], .headline-2 a[href]")
    href = (context or title_link or {}).get("href") if (context or title_link) is not None else None
    owner_link = node.select_one("a.avatar[href]") or node.select_one("a.owner[href]")
    owner_name = clean(node.select_one(".displayname"))
    member = member_from_link(owner_link, name=owner_name)
    if member is None and href:
        segs = path_segments(href)
        member = {"username": segs[0], "name": owner_name or segs[0], "link": f"{SITE}/{segs[0]}/", "avatar": None} if segs else None
    poster = node.select_one("div.react-component[data-component-class='LazyPoster']")
    card = film_from_poster(poster) if poster is not None else None
    if card is None and film is not None:
        card = dict(film)
    if card is not None and href:
        segs = path_segments(href)
        if len(segs) >= 3 and segs[1] == "film" and not card.get("slug"):
            card["slug"] = segs[2]
    date_node = node.select_one("time[datetime]")
    context_text = (clean(context) or "").lower()
    body = node.select_one(".js-review-body")
    truncated = body is not None and body.select_one(".collapsed-text") is not None
    review_text, review_html = html_text(detached(body)) if body is not None else (None, None)
    likes = node.select_one(".like-link-target[data-count]")
    comments = node.select_one("a.metadata[href*='#comments'] .label")
    return {
        "id": viewing_id,
        "link": absolute(href),
        "member": member,
        "film": card,
        "rating": svg_rating(node),
        "is_liked": has_liked_icon(node.select_one(".content-reactions-strip") or node),
        "is_rewatch": "rewatch" in context_text,
        "date": iso_date(date_node.get("datetime")) if date_node is not None else None,
        "date_type": ("watched" if "watch" in context_text else "reviewed" if context_text else None),
        "has_spoilers": node.select_one(".js-spoiler-container") is not None or "may contain spoilers" in (clean(node) or ""),
        "text": review_text,
        "html": review_html,
        "is_text_truncated": truncated,
        "language": body.get("lang") if body is not None else None,
        "likes_count": to_int(likes.get("data-count")) if likes is not None else None,
        "comments_count": count(clean(comments)) if comments is not None else 0,
        "_full_text": body.get("data-full-text-url") if truncated else None,
    }


def review_items(doc, film=None):
    return [r for r in (review_item(a, film) for a in doc.select("article.production-viewing")) if r]


def full_text(markup):
    """/s/full-text/viewing:<id>/ -> (text, html)."""
    doc = soup(markup)
    body = doc.body or doc
    return html_text(body)


def viewing_json(data):
    """/viewing/<id>/json/ (or /<member>/film/<slug>/json/) -> entry facts."""
    if not isinstance(data, dict) or not data.get("viewingId"):
        return None
    film = data.get("viewingable") or {}
    review_html = data.get("reviewText")
    return {
        "id": to_int(data.get("viewingId")),
        "short_link": short_link(data.get("viewingLid")),
        "film": film_json(film),
        "rating": rating_from_ten(data.get("rating")),
        "is_liked": bool(data.get("liked")),
        "is_rewatch": bool(data.get("rewatch")),
        "watched_date": data.get("viewingDate") if data.get("specifiedDate") else None,
        "has_spoilers": bool(data.get("containsSpoilers")),
        "tags": [t for t in data.get("tags") or [] if isinstance(t, str)],
        "review_html": review_html,
        "is_draft": bool(data.get("draft")),
        "privacy": (data.get("privacyPolicy") or "").lower() or None,
    }


def review_page(markup):
    """/<member>/film/<slug>/[<n>/] -> what /viewing/<id>/json/ lacks:
    author, likes, tags, watched date, the comments fragment path."""
    doc = soup(markup)
    author = person_summary(doc.select_one(".person-header .person-summary"))
    likes = doc.select_one(".review-like[data-count]") or doc.select_one(".like-link-target[data-count]")
    ident = json_attr(likes, "data-likeable-identifier") if likes is not None else {}
    viewing = None
    m = _UID_RE.search(ident.get("uid") or "")
    if m and m.group(1) == "viewing":
        viewing = int(m.group(2))
    if viewing is None:
        m = re.search(r"/csi/viewing/(\d+)/", markup or "")
        viewing = int(m.group(1)) if m else None
    watched = None
    date_links = doc.select(".view-date a[href]")
    for a in date_links:
        nums = [x for x in path_segments(a.get("href")) if x.isdigit()]
        if len(nums) >= 3:
            watched = f"{nums[-3]}-{int(nums[-2]):02d}-{int(nums[-1]):02d}"
    date_label = (clean(doc.select_one(".view-date")) or "").lower()
    comments_src = next((n.get("data-src") for n in doc.select("[data-src*='comments-section']")), None)
    tags = [clean(a) for a in doc.select("ul.tags a[href]") if clean(a)]
    return {
        "viewing_id": viewing,
        "short_link": short_link(ident.get("lid")),
        "member": author,
        "likes_count": to_int(likes.get("data-count")) if likes is not None else None,
        "watched_date": watched,
        "is_rewatch": "rewatched" in date_label,
        "tags": tags,
        "_comments_src": comments_src,
    }


# ---- diary -----------------------------------------------------------------------

def diary_rows(doc):
    out = []
    for row in doc.select("tr.diary-entry-row"):
        poster = row.select_one("div.react-component[data-component-class='LazyPoster']")
        film = film_from_poster(poster)
        day = row.select_one("a.daydate[href]")
        date = None
        if day is not None:
            segs = path_segments(day.get("href"))
            nums = [s for s in segs if s.isdigit()]
            if len(nums) >= 3:
                date = f"{nums[-3]}-{int(nums[-2]):02d}-{int(nums[-1]):02d}"
        link = row.select_one("h2.primaryname a[href], h3 a[href]")
        rewatch = row.select_one("td.col-rewatch")
        review = row.select_one("td.col-review")
        review_link = review.select_one("a[href]") if review is not None else None
        like = row.select_one("td.col-like")
        out.append({
            "id": to_int(row.get("data-viewing-id")),
            "date": date,
            "link": absolute(link.get("href")) if link is not None else None,
            "film": film,
            "rating": rating_from_class(row.select_one("td.col-rating .rating")),
            "is_liked": bool(like.select_one(".icon-liked")) if like is not None else False,
            "is_rewatch": rewatch is not None and "icon-status-off" not in (rewatch.get("class") or []),
            "has_review": review is not None and "icon-status-off" not in (review.get("class") or []),
            "review_link": absolute(review_link.get("href")) if review_link is not None else None,
        })
    return out


# ---- lists -------------------------------------------------------------------------

def list_summary(node):
    """article.list-summary -> list card."""
    if node is None:
        return None
    title = node.select_one(".name a[href], h2 a[href]")
    if title is None:
        return None
    href = title.get("href")
    segs = path_segments(href)
    owner_link = node.select_one(".attribution-block a.owner[href], .attribution-block a.avatar[href]")
    owner_name = clean(node.select_one(".attribution-block .displayname"))
    owner = member_from_link(owner_link, name=owner_name,
                             img=node.select_one(".attribution-block a.avatar img")) if owner_link is not None else (
        {"username": segs[0], "name": segs[0], "link": f"{SITE}/{segs[0]}/", "avatar": None} if segs else None)
    strip = node.select_one(".content-reactions-strip")
    films_count = likes = comments = None
    if strip is not None:
        value = strip.select_one(".value")
        films_count = count(clean(value)) if value is not None else None
        for a in strip.select("a.metadata[href]"):
            label = count(clean(a.select_one(".label")))
            if "/likes/" in a.get("href"):
                likes = label
            elif "#comments" in a.get("href"):
                comments = label
    notes = node.select_one(".notes")
    description, description_html = html_text(detached(notes)) if notes is not None else (None, None)
    previews = [film for film, _ in film_cards(node.select_one(".poster-list-overlapped, .posterset"))]
    for film in previews:
        film.pop("_poster", None)
        film.pop("poster", None)
    return {
        "id": to_int(node.get("data-film-list-id")),
        "slug": segs[2] if len(segs) >= 3 else None,
        "name": clean(title),
        "link": absolute(href),
        "owner": owner,
        "description": description,
        "films_count": films_count,
        "likes_count": likes,
        "comments_count": comments if comments is not None else 0,
        "preview_films": previews,
    }


def list_summaries(doc):
    return [s for s in (list_summary(n) for n in doc.select("article.list-summary")) if s]


def list_page(markup, path):
    """/<member>/list/<slug>/detail/ -> list metadata + entries."""
    doc = soup(markup)
    sidebar = doc.select_one("[data-component-class='ListSidebar']")
    segs = [s for s in path.split("/") if s]
    ident = json_attr(sidebar, "data-list-identifier") if sidebar is not None else {}
    notes = doc.select_one("#list-notes") or doc.select_one(".list-title-intro .body-text")
    description, description_html = html_text(detached(notes)) if notes is not None else (None, None)
    meta_desc = (doc.find("meta", attrs={"name": "description"}) or {}).get("content") if doc.find("meta", attrs={"name": "description"}) else None
    m = re.match(r"A (?:ranked )?list of ([\d,]+) film", html_lib.unescape(meta_desc or ""))
    films_count = count(m.group(1)) if m else None
    published = doc.select_one(".list-date .published time[datetime]")
    updated = doc.select_one(".list-date .updated time[datetime]")
    tags = [clean(a) for a in doc.select("ul.tags a[href]") if clean(a)]
    list_id = None
    m = re.search(r"/csi/list/(\d+)/", markup or "")
    if m:
        list_id = int(m.group(1))
    owner_username = (sidebar.get("data-owner") if sidebar is not None else None) or segs[0]
    owner = None
    for a in doc.select("a.avatar[href], a.name[href]"):
        if path_segments(a.get("href"))[:1] == [owner_username.lower()]:
            owner = member_from_link(a)
            break
    if owner is None:
        owner = {"username": owner_username.lower(), "name": owner_username, "link": f"{SITE}/{owner_username.lower()}/", "avatar": None}
    owner_name = clean(doc.select_one(".list-title-intro .attribution .name, .person-summary .name"))
    if owner_name and owner.get("name") == owner.get("username"):
        owner["name"] = owner_name
    return {
        "id": list_id,
        "slug": segs[2] if len(segs) >= 3 else None,
        "name": (sidebar.get("data-list-name") if sidebar is not None else None) or clean(doc.select_one(".list-title-intro h1")),
        "link": f"{SITE}/{'/'.join(segs[:3])}/",
        "short_link": (sidebar.get("data-list-boxdit-url") if sidebar is not None else None) or short_link(ident.get("lid")),
        "owner": owner,
        "description": description,
        "description_html": description_html,
        "tags": tags,
        "is_ranked": bool(doc.select_one(".numbered-list-item, .list-detailed-entry.-numbered, .list-number")),
        "films_count": films_count,
        "likes_count": to_int(sidebar.get("data-like-count")) if sidebar is not None else None,
        "clones_count": to_int(sidebar.get("data-clone-count")) if sidebar is not None else None,
        "published_at": iso_datetime(published.get("datetime")) if published is not None else None,
        "updated_at": iso_datetime(updated.get("datetime")) if updated is not None else None,
    }


def list_detail_entries(doc):
    """/detail/ view entries -> films with position, owner rating and notes."""
    out = []
    for node in doc.select("article.list-detailed-entry, li.list-detailed-entry, .list-detailed-entry"):
        poster = node.select_one("div.react-component[data-component-class='LazyPoster']")
        film = film_from_poster(poster)
        if not film:
            continue
        position = node.select_one(".position, .list-number")
        notes = node.select_one(".js-review-body, .body-text")
        notes_text, notes_html = html_text(detached(notes)) if notes is not None else (None, None)
        film["position"] = to_int(clean(position)) if position is not None else None
        film["owner_rating"] = svg_rating(node.select_one(".content-reactions-strip")) if node.select_one(".content-reactions-strip") else None
        film["is_liked_by_owner"] = has_liked_icon(node.select_one(".content-reactions-strip"))
        film["notes"] = notes_text
        film["notes_html"] = notes_html
        film["has_spoilers"] = node.select_one(".js-spoiler-container") is not None
        out.append(film)
    return out


# ---- comments ----------------------------------------------------------------------

def comments(doc):
    out = []
    for li in doc.select("li.comment[data-comment-id]"):
        person = li.select_one(".js-comment-person, .person-summary")
        link = person.select_one("strong.name a[href]") if person is not None else None
        member = member_from_link(link or (person.select_one("a.avatar[href]") if person is not None else None),
                                  name=clean(link), img=person.select_one("img") if person is not None else None)
        body = li.select_one(".comment-body")
        body_text, body_html = html_text(detached(body)) if body is not None else (None, None)
        permalink = li.select_one("a.comment-permalink[href]")
        edited = clean(li.select_one(".edited-label"))
        out.append({
            "id": to_int(li.get("data-comment-id")),
            "link": absolute(permalink.get("href")) if permalink is not None else None,
            "member": member,
            "text": body_text,
            "html": body_html,
            "created_at": ms_datetime(li.get("data-creation-timestamp")),
            "is_edited": bool(edited),
        })
    return out


def comments_meta(doc):
    """comments-section fragment -> (total listed, start index of the newest
    page, the ajax data-url)."""
    panel = doc.select_one("#comments")
    ul = doc.select_one("ul.comment-list")
    total = to_int(ul.get("data-num-comments")) if ul is not None else None
    start = to_int(ul.get("data-comments-start")) if ul is not None else None
    heading = clean(doc.select_one("#comments .section-heading"))
    return {"count": total, "start": start, "heading": heading,
            "data_url": panel.get("data-url") if panel is not None else None}


# ---- people ------------------------------------------------------------------------

def person_page(markup, role, slug):
    doc = soup(markup)
    title = doc.select_one("h1.contextual-title .name, .contextual-title .name")
    roles = []
    for li in doc.select("#content-nav .smenu-wrapper-left .smenu-menu li"):
        a = li.select_one("a[href]")
        sel = li.select_one(".selected")
        label_node = a or sel
        if label_node is None:
            continue
        small = label_node.select_one("small")
        films = to_int(clean(small)) if small is not None else None
        if small is not None:
            small.extract()
        href = a.get("href") if a is not None else f"/{role}/{slug}/"
        rsegs = path_segments(href)
        roles.append({"role": rsegs[0] if rsegs else role, "label": clean(label_node),
                      "films_count": films, "link": absolute(href)})
    image = doc.select_one(".contributor-image img, img.js-tmdb-person")
    bio_node = doc.select_one(".bio .js-tmdb-bio, .bio .body-text, .bio")
    bio_text, _ = html_text(detached(bio_node)) if bio_node is not None else (None, None)
    tmdb_id = to_int(image.get("data-tmdb-id")) if image is not None else None
    if tmdb_id is None:
        m = re.search(r"themoviedb\.org/person/(\d+)", markup or "")
        tmdb_id = int(m.group(1)) if m else None
    films = grid_films(doc)
    return {
        "slug": slug,
        "name": clean(title),
        "role": role,
        "link": f"{SITE}/{role}/{slug}/",
        "photo": (image.get("data-image") if image is not None else None) or None,
        "bio": bio_text,
        "tmdb_id": tmdb_id,
        "tmdb_link": f"https://www.themoviedb.org/person/{tmdb_id}/" if tmdb_id else None,
        "roles": roles,
        "films_count": len(films),
        "films": films,
    }


def role_counts(markup, current_role):
    """Role menu of a person page -> {role: films count} (the selected
    entry has no link, so it is keyed by `current_role`)."""
    doc = soup(markup)
    out = {}
    for li in doc.select("#content-nav .smenu-wrapper-left .smenu-menu li"):
        a = li.select_one("a[href]")
        node = a or li.select_one(".selected")
        if node is None:
            continue
        small = node.select_one("small")
        segs = path_segments(a.get("href")) if a is not None else []
        out[segs[0] if segs else current_role] = to_int(clean(small)) if small is not None else 0
    return out


# ---- search ------------------------------------------------------------------------

def search_cursor(doc):
    ul = doc.select_one("ul.results[data-cursor]")
    return unquote(ul.get("data-cursor")) if ul is not None and ul.get("data-cursor") else None


def search_film(li):
    poster = li.select_one("div.react-component[data-component-class='LazyPoster']")
    film = film_from_poster(poster)
    if not film:
        return None
    meta = li.select_one(".film-metadata.truncate, .truncate.film-metadata") or li.select_one(".truncate")
    original = alternatives = None
    if meta is not None:
        raw = meta.get_text("\n", strip=True)
        for line in raw.split("\n"):
            line = " ".join(line.split())
            if line.lower().startswith("original title:"):
                original = line.split(":", 1)[1].strip() or None
            elif line.lower().startswith("alternative title"):
                alternatives = [t.strip() for t in line.split(":", 1)[1].split(",") if t.strip()]
    directors = []
    for p in li.select("p.film-metadata"):
        if (clean(p) or "").lower().startswith("directed by"):
            directors = _people(p)
    film["original_name"] = original
    film["alternative_titles"] = alternatives or []
    film["directors"] = directors
    return film


def search_contributor(li):
    a = li.select_one("h2 a[href]")
    if a is None:
        return None
    segs = path_segments(a.get("href"))
    meta = li.select_one("p.film-metadata")
    known_for = _slug_links(meta) if meta is not None else []
    aka = None
    trunc = li.select_one(".truncate")
    if trunc is not None:
        raw = clean(trunc) or ""
        if raw.lower().startswith("also known as:"):
            aka = [t.strip() for t in raw.split(":", 1)[1].split(",") if t.strip()]
    m = re.search(r"of ([\d,]+) films", clean(meta) or "") if meta is not None else None
    return {
        "name": clean(a),
        "slug": segs[1] if len(segs) > 1 else None,
        "role": segs[0] if segs else None,
        "link": absolute(a.get("href")),
        "films_count": count(m.group(1)) if m else (len(known_for) or None),
        "known_for": [{"name": k["name"], "slug": k["slug"], "link": k["link"]} for k in known_for],
        "also_known_as": aka or [],
    }


def search_member(li):
    member = person_summary(li.select_one(".person-summary") or li)
    return member


def search_tag(li):
    a = li.select_one("h2 a[href]")
    if a is None:
        return None
    segs = path_segments(a.get("href"))
    return {"name": clean(a), "slug": unquote(segs[1]) if len(segs) > 1 else None, "link": absolute(a.get("href"))}


# ---- activity ------------------------------------------------------------------------

def _activity_target(a):
    """The row's a.target -> (target dict, film card | None)."""
    if a is None:
        return None, None
    href = a.get("href") or ""
    segs = path_segments(href)
    node = BeautifulSoup(str(a), "lxml").select_one("a")
    context = " ".join(clean(c) or "" for c in node.select(".context")).strip() or None
    for c in node.select(".context, .rating"):
        c.decompose()
    name = clean(node)
    film = None
    if segs[:1] == ["film"] and len(segs) >= 2:
        kind, film = "film", {"slug": segs[1], "name": name, "link": absolute(f"/film/{segs[1]}/")}
    elif len(segs) >= 3 and segs[1] == "film":
        kind = "review" if context and "review" in context.lower() else "diary_entry"
        film = {"slug": segs[2], "name": name, "link": absolute(f"/film/{segs[2]}/")}
    elif len(segs) >= 3 and segs[1] == "list":
        kind = "list"
    elif len(segs) == 1:
        kind = "member"
    else:
        kind = None
    return {"type": kind, "name": name, "link": absolute(href.split("#")[0])}, film


_ACTIVITY_VERBS = (
    ("rewatched", "watched"), ("watched", "watched"), ("reviewed", "reviewed"), ("rated", "rated"),
    ("liked", "liked"), ("added", "added_to_watchlist"), ("commented", "commented"),
    ("cloned", "list_cloned"), ("listed", "list_created"), ("updated", "list_updated"),
    ("followed", "followed"),
)


def activity(doc):
    """/ajax/activity-pagination/<m>/ rows -> activity items."""
    out = []
    for sec in doc.select("section.activity-row[data-activity-id]"):
        summary = sec.select_one(".activity-summary") or sec.select_one(".table-activity-description")
        target, film = _activity_target(sec.select_one("a.target[href]"))
        if film is None and (target or {}).get("type") != "list":
            poster = sec.select_one("div.react-component[data-component-class='LazyPoster']")
            card = film_from_poster(poster) if poster is not None else None
            if card:
                film = {"slug": card["slug"], "name": card["name"], "link": card["link"]}
        summary_text = clean(summary) if summary is not None else None
        low = f" {(summary_text or '').lower()} "
        kind = next((name for verb, name in _ACTIVITY_VERBS if f" {verb}" in low), None)
        if kind is None:
            kind = next((c[1:] for c in sec.get("class") or [] if c.startswith("-")), None)
        if kind == "liked" and target and target.get("type") in ("list", "review"):
            kind = f"liked_{target['type']}"
        comment = sec.select_one(".activity-comment-text")
        comment_text = html_text(detached(comment))[0] \
            if comment is not None else None
        ts = sec.select_one("time[datetime]")
        out.append({
            "id": to_int(sec.get("data-activity-id")),
            "type": kind,
            "summary": summary_text,
            "rating": rating_from_class(sec.select_one(".activity-summary .rating, .rating")),
            "target": target,
            "film": film,
            "comment": comment_text,
            "created_at": iso_datetime(ts.get("datetime")) if ts is not None else None,
        })
    return out


# ---- availability ------------------------------------------------------------------

def _decode_cx(view_url):
    """JustWatch click link -> (destination link, price, currency, monetization)."""
    if not view_url:
        return None, None, None, None
    q = parse_qs(urlparse(view_url).query)
    dest = (q.get("r") or [None])[0]
    price = currency = kind = None
    raw = (q.get("cx") or [None])[0]
    if raw:
        try:
            padded = raw + "=" * (-len(raw) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", "replace"))
            for ctx in payload.get("data") or []:
                data = ctx.get("data") or {}
                if "currency" in data or "price" in data:
                    price = data.get("price")
                    currency = data.get("currency")
                    kind = data.get("monetizationType")
        except Exception:
            pass
    return dest, price, currency, kind


def availability(data):
    """/s/production-availabilities JSON -> {format: {stream|rent|buy: [...]}}."""
    out = {}
    if not isinstance(data, dict):
        return out
    for fmt in ("best", "sd", "hd", "4k"):
        group = data.get(fmt) or {}
        block = {}
        for kind in ("stream", "rent", "buy"):
            rows = []
            for row in group.get(kind) or []:
                if not isinstance(row, dict):
                    continue
                dest, price, currency, monetization = _decode_cx(row.get("viewUrl"))
                if price is None:
                    m = re.search(r"([\d.,]+)", row.get("formattedPrice") or "")
                    price = to_float(m.group(1).replace(",", "")) if m else None
                rows.append({
                    "service": text(row.get("name")),
                    "link": dest or row.get("viewUrl"),
                    "icon": row.get("icon"),
                    "format": (row.get("format") or fmt).lower(),
                    "monetization": (monetization or row.get("type") or kind).lower(),
                    "price": to_float(price),
                    "currency": currency if price is not None else None,
                })
            block[kind] = rows
        out[fmt] = block
    return out


# ---- member stats -------------------------------------------------------------------

def summary_statistics(doc):
    """.yir-member-stats h4s -> {snake label: number | text}."""
    out = {}
    for h4 in doc.select(".yir-member-stats .yir-member-statistic"):
        label = clean(h4.select_one(".definition"))
        value = clean(h4.select_one(".value"))
        if not label:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", label.lower().replace("2+", "two_plus")).strip("_")
        number = to_float((value or "").replace(",", "")) if value and re.match(r"^[\d.,]+$", value) else None
        if number is not None:
            out[key] = int(number) if number.is_integer() else number
        else:
            m = re.match(r"^(\d+)([wd])$", value or "")
            out[key] = {"value": int(m.group(1)), "unit": "weeks" if m.group(2) == "w" else "days"} if m else value
    return out


def breakdown(markup):
    """/csi/person/<m>/stats/genres/ -> {most_watched|highest_rated: {genres,
    countries, languages: [{name, slug, films_count | average_rating}]}}."""
    doc = soup(markup)
    out = {}
    for section in doc.select("section.js-yir-tab-section[id]"):
        sid = section.get("id") or ""
        if not sid.startswith("genres-"):
            continue
        tab = "most_watched" if "most-watched" in sid else "highest_rated"
        groups = {}
        for group in section.select("section.yir-genres, section.yir-countries, section.yir-languages"):
            name = next(c[4:] for c in group.get("class") if c.startswith("yir-"))
            rows = []
            for bar in group.select(".film-breakdown-graph-bar"):
                label = bar.select_one(".film-breakdown-graph-bar-label")
                value = bar.select_one(".film-breakdown-graph-bar-value")
                segs = path_segments(label.get("href")) if label is not None else []
                key = next((i for i, x in enumerate(segs) if x in ("genre", "country", "language")), None)
                row = {"name": clean(label),
                       "slug": segs[key + 1] if key is not None and key + 1 < len(segs) else None}
                val_text = clean(value) or ""
                if tab == "most_watched":
                    row["films_count"] = to_int(value.get("data-count")) if value is not None else count(val_text)
                else:
                    m = re.search(r"Average\s+([\d.]+)", val_text)
                    row["average_rating"] = to_float(m.group(1)) if m else (
                        to_float(value.get("data-count")) if value is not None else None)
                rows.append(row)
            groups[name] = rows
        out[tab] = groups
    return out


def person_leaders(markup):
    """actors-/directors-most-watched|highest-rated fragment -> people."""
    doc = soup(markup)
    out = []
    for li in doc.select("ul.avatar-list > li"):
        name = li.select_one(".yir-secondary-heading a")
        label = clean(li.select_one(".yir-label"))
        img = li.select_one("img")
        href = name.get("href") if name is not None else None
        segs = path_segments(href)
        slug = None
        if "with" in segs:
            i = segs.index("with")
            slug = segs[i + 2] if i + 2 < len(segs) else None
        row = {
            "name": clean(name),
            "slug": slug,
            "link": absolute(f"/{segs[segs.index('with') + 1]}/{slug}/") if slug and "with" in segs else None,
            "photo": img.get("data-image") if img is not None else None,
            "tmdb_id": to_int(img.get("data-tmdb-id")) if img is not None else None,
        }
        m = re.search(r"([\d,]+)\s*film", label or "")
        row["films_count"] = count(m.group(1)) if m else None
        m = re.search(r"★\s*([\d.]+)", label or "")
        if m:
            row["average_rating"] = to_float(m.group(1))
        out.append(row)
    return out


def most_watched_films(markup):
    """highs-and-lows fragment 'Most Watched' block -> films + times."""
    doc = soup(markup)
    out = []
    for cell in doc.select(".yir-most-rewatched .yir-poster-grid"):
        film = grid_film(cell.select_one(".poster-container") or cell)
        if not film:
            continue
        times = re.search(r"(\d+)\s*times", clean(cell.select_one(".yir-label")) or "")
        film["times_watched"] = int(times.group(1)) if times else None
        out.append(film)
    return out


def decades(markup):
    """decades fragment -> [{decade, average_rating, top_films}]."""
    doc = soup(markup)
    out = []
    for box in doc.select(".yir-decade-container"):
        label = clean(box.select_one(".yir-decade-year a, .yir-decade-year span"))
        avg = re.search(r"Average ([\d.]+)", clean(box.select_one(".decade-avg")) or "")
        films = [f for f in (grid_film(p) for p in box.select(".poster-container")) if f]
        out.append({"decade": label, "average_rating": to_float(avg.group(1)) if avg else None,
                    "top_films": films})
    return out


# ---- tags --------------------------------------------------------------------------

def member_tags(doc):
    out = []
    for a in doc.select("ul.tags-columns a[href], ul.tag-list a[href], .tags a[href*='/tag/']"):
        segs = path_segments(a.get("href"))
        if "tag" not in segs:
            continue
        i = segs.index("tag")
        count_node = a.find_next_sibling("span") or a.select_one(".count")
        out.append({"name": clean(a.select_one(".name")) or clean(a), "slug": segs[i + 1] if i + 1 < len(segs) else None,
                    "link": absolute(a.get("href")),
                    "count": count(clean(count_node)) if count_node is not None else None})
    return out


# ---- RSS ---------------------------------------------------------------------------

def rss_items(markup):
    doc = BeautifulSoup(markup or "", "xml")
    return doc.find_all("item")


def journal_items(markup):
    out = []
    for item in rss_items(markup):
        desc = item.find("description")
        desc_doc = soup(desc.text if desc is not None else "")
        img = desc_doc.select_one("img")
        paragraphs = [clean(p) for p in desc_doc.select("p")]
        link = clean(item.find("link"))
        out.append({
            "title": clean(item.find("title")),
            "link": link,
            "slug": path_segments(link)[-1] if link else None,
            "category": clean(item.find("category")),
            "summary": "\n\n".join(p for p in paragraphs if p) or None,
            "image": img.get("src") if img is not None else None,
            "published_at": rfc822_datetime(clean(item.find("pubDate"))),
        })
    return out
