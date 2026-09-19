"""Offline tests: refs parsing and the HTML / JSON normalizers against
pages captured 2026-09-19 (letterboxd/fixtures/, scripts and SVG paths
stripped; SVG aria-labels kept — ratings live there).

    python -m pytest letterboxd/test_parsers.py -q
"""
import json
import os

import pytest

from letterboxd import parsers as P
from letterboxd import refs
from letterboxd.shared import path, rating_segment

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def doc(name):
    return P.soup(fx(name + ".html"))


# ---- refs ----------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("parasite-2019", {"slug": "parasite-2019"}),
    ("1917", {"slug": "1917"}),
    ("https://letterboxd.com/film/parasite-2019/reviews/by/activity/", {"slug": "parasite-2019"}),
    ("https://letterboxd.com/getout/film/parasite-2019/", {"slug": "parasite-2019"}),
    ("https://boxd.it/hTha", {"short_link": "https://boxd.it/hTha"}),
    ("tt6751668", {"imdb_id": "tt6751668"}),
    ("imdb:tt6751668", {"imdb_id": "tt6751668"}),
    ("https://www.imdb.com/title/tt6751668/?ref_=x", {"imdb_id": "tt6751668"}),
    ("tmdb:496243", {"tmdb_id": "496243"}),
    ("https://www.themoviedb.org/movie/496243-parasite", {"tmdb_id": "496243"}),
])
def test_resolve_film(value, expected):
    full = {"slug": None, "short_link": None, "imdb_id": None, "tmdb_id": None, **expected}
    assert refs.resolve_film(value) == full


@pytest.mark.parametrize("value", ["https://www.themoviedb.org/tv/1399", "https://open.spotify.com/x",
                                   "https://letterboxd.com/dave/", "Not A Slug!"])
def test_resolve_film_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_film(value)


def test_resolve_member_list_review_person():
    assert refs.resolve_member("https://letterboxd.com/Dave/films/diary/") == {"username": "dave"}
    with pytest.raises(ValueError):
        refs.resolve_member("films")
    assert refs.resolve_list("dave/imdb-top-250") == {"username": "dave", "slug": "imdb-top-250", "short_link": None}
    assert refs.resolve_list("https://letterboxd.com/dave/list/imdb-top-250/detail/")["slug"] == "imdb-top-250"
    assert refs.resolve_list("https://boxd.it/7jLA")["short_link"] == "https://boxd.it/7jLA"
    assert refs.resolve_review("getout/parasite-2019") == {"username": "getout", "film_slug": "parasite-2019",
                                                           "entry": None, "short_link": None}
    assert refs.resolve_review("https://letterboxd.com/dave/film/the-avengers-2012/2/")["entry"] == 2
    assert refs.resolve_person("director/bong-joon-ho") == {"role": "director", "slug": "bong-joon-ho"}
    assert refs.resolve_person("bong-joon-ho") == {"role": None, "slug": "bong-joon-ho"}
    assert refs.resolve_person("https://letterboxd.com/actor/song-kang-ho/by/rating/")["role"] == "actor"
    assert refs.resolve_theme("https://letterboxd.com/films/mini-theme/abc-def/by/best-match/") == \
        {"kind": "mini-theme", "slug": "abc-def"}
    assert refs.resolve_collection("https://letterboxd.com/films/in/toy-story-collection/") == "toy-story-collection"
    assert refs.resolve_tag("Criterion Channel") == "criterion-channel"


def test_geo_and_genres():
    assert refs.country_slug("KR") == refs.country_slug("South Korea") == "south-korea"
    assert refs.country_slug("US") == "usa"
    assert refs.language_slug("ko") == refs.language_slug("Korean") == "korean"
    assert refs.availability_locale("gb") == "GBR" and refs.availability_locale("USA") == "USA"
    assert refs.genre_segment(["horror", "-comedy", "Science Fiction"]) == "horror+-comedy+science-fiction"
    with pytest.raises(ValueError):
        refs.genre_segment(["noir"])


def test_url_helpers():
    assert path("dave", "films", None, "rated/4", "", page=2) == "/dave/films/rated/4/page/2/"
    assert rating_segment(3.5) == "3½" and rating_segment(0.5) == "½" and rating_segment(4.0) == "4"


# ---- films ---------------------------------------------------------------------------

def test_film_page():
    film = P.film_page(fx("film_page.html"))
    assert film["id"] == 426406 and film["slug"] == "parasite-2019" and film["year"] == 2019
    assert film["original_name"] == "기생충" and film["runtime_minutes"] == 133
    assert film["short_link"] == "https://boxd.it/hTha"
    assert film["cast"][0] == {"name": "Song Kang-ho", "slug": "song-kang-ho", "character": "Kim Ki-taek",
                               "link": "https://letterboxd.com/actor/song-kang-ho/"}
    assert film["crew"][0]["role"] == "director"
    assert film["primary_language"]["slug"] == "korean" and film["countries"][0]["slug"] == "south-korea"
    assert {"premiere", "theatrical"} <= {r["type"] for r in film["releases"]}
    assert film["external_ids"]["imdb"]["id"] == "tt6751668" and film["external_ids"]["tmdb"]["id"] == 496243
    assert film["trailer"]["youtube_id"] and film["themes"][0]["type"] == "theme"
    assert film["rating"]["average"] and film["similar_films"]


def test_film_stats_histogram():
    assert P.film_stats(fx("film_stats.html"))["top_500_rank"] == 15
    hist = P.histogram(fx("film_histogram.html"))
    assert len(hist["distribution"]) == 10 and hist["distribution"][-1]["rating"] == 5.0
    assert hist["average"] == 4.52 and hist["count"] > 5_000_000


def test_reviews():
    items = P.review_items(doc("film_reviews"))
    assert len(items) == 12
    first = items[0]
    assert first["id"] and first["member"]["username"] and first["rating"] in (0.5 * i for i in range(1, 11))
    assert first["likes_count"] > 0 and first["date"]
    assert any(r["has_spoilers"] for r in items)
    member_items = P.review_items(doc("member_reviews"))
    assert member_items[0]["film"]["slug"] and member_items[0]["_full_text"]


def test_viewing_json_and_full_text():
    entry = P.viewing_json(json.loads(fx("viewing_json.json")))
    assert entry["rating"] == 5.0 and entry["is_liked"] and entry["watched_date"] == "2019-08-14"
    assert entry["film"]["slug"] == "parasite-2019" and entry["tags"]
    text, html = P.full_text(fx("full_text.html"))
    assert text.startswith("Update") and "<p>" in html


def test_review_page():
    page = P.review_page(fx("review_page.html"))
    assert page["viewing_id"] == 72286970 and page["member"]["username"] == "philbertdy"
    assert page["likes_count"] > 40000 and page["watched_date"] == "2019-08-14"


def test_availability():
    data = P.availability(json.loads(fx("availability.json")))
    rent = data["best"]["rent"][0]
    assert rent["price"] and rent["currency"] == "USD" and not rent["link"].startswith("https://click.justwatch")


def test_browse_and_grid():
    films = P.grid_films(doc("browse"))
    assert len(films) == 72 and films[0]["average_rating"] and films[0]["_poster"][1].startswith("/film/")


# ---- members -------------------------------------------------------------------------

def test_profile():
    prof = P.profile(fx("member_profile.html"), "dave")
    assert prof["name"] == "Dave Vis" and prof["badge"] == "patron"
    assert prof["stats"]["films"] > 2000 and len(prof["favorite_films"]) == 4
    assert prof["ratings"]["distribution"] and prof["ratings"]["average"]


def test_diary():
    rows = P.diary_rows(doc("member_diary"))
    assert len(rows) == 50 and rows[0]["date"] and rows[0]["film"]["slug"]
    assert any(r["is_rewatch"] for r in rows) and any(r["is_liked"] for r in rows)
    assert P.pages_total(doc("member_diary")) > 1


def test_member_tables():
    followers = P.member_rows(doc("member_followers"))
    assert len(followers) == 25 and followers[0]["films_count"] is not None
    members = P.member_rows(doc("film_members"))
    assert members[0]["rating"] is not None


def test_activity():
    items = P.activity(doc("activity"))
    assert len(items) == 20 and items[0]["id"] and items[0]["created_at"]
    kinds = {i["type"] for i in items}
    assert "watched" in kinds and any(i["rating"] for i in items)


def test_stats():
    summary = P.summary_statistics(doc("member_stats"))
    assert summary["films"] > 2000 and summary["longest_streak"]["unit"] == "weeks"
    breakdown = P.breakdown(fx("stats_genres.html"))
    assert breakdown["most_watched"]["genres"][0]["films_count"]
    assert breakdown["highest_rated"]["genres"][0]["average_rating"]
    assert P.person_leaders(fx("stats_actors.html"))[0]["tmdb_id"]
    assert P.most_watched_films(fx("stats_highs.html"))[0]["times_watched"]
    assert P.decades(fx("stats_decades.html"))[0]["average_rating"]


def test_member_tags():
    tags = P.member_tags(doc("member_tags"))
    assert tags and tags[0]["count"]


# ---- lists / comments ------------------------------------------------------------------

def test_lists():
    cards = P.list_summaries(doc("popular_lists"))
    assert len(cards) == 18 and cards[0]["films_count"] and cards[0]["owner"]["username"]
    meta = P.list_page(fx("list_detail.html"), "/dave/list/imdb-top-250/")
    assert meta["films_count"] == 250 and meta["likes_count"] and meta["short_link"] == "https://boxd.it/7jLA"
    assert meta["description_html"].startswith("<p>")
    entries = P.list_detail_entries(doc("list_detail"))
    assert len(entries) == 100 and entries[0]["position"] == 1 and entries[0]["owner_rating"]


def test_comments():
    section = doc("list_comments")
    rows = P.comments(section)
    assert len(rows) == 20 and rows[0]["member"]["username"] and rows[0]["created_at"]
    meta = P.comments_meta(section)
    assert meta["count"] and meta["start"]


# ---- search / people / journal ---------------------------------------------------------

def test_search():
    films = [P.search_film(li) for li in doc("search_films").select("li.search-result")]
    assert films[0]["slug"] == "parasite-2019" and films[0]["directors"][0]["slug"] == "bong-joon-ho"
    people = [P.search_contributor(li) for li in doc("search_cast_crew").select("li.search-result")]
    assert any(p["role"] == "director" and p["films_count"] for p in people)
    members = [P.search_member(li) for li in doc("search_members").select("li.search-result")]
    assert members[0]["username"] == "dave" and members[0]["badge"] == "patron"
    tags = [P.search_tag(li) for li in doc("search_tags").select("li.search-result")]
    assert tags and "%" not in tags[0]["slug"]
    assert P.review_items(doc("search_reviews"))
    assert P.search_cursor(doc("search_films"))


def test_person():
    person = P.person_page(fx("person.html"), "director", "bong-joon-ho")
    assert person["name"] == "Bong Joon Ho" and person["tmdb_id"] == 21684
    assert person["films_count"] == 18 and {r["role"] for r in person["roles"]} >= {"director", "actor"}
    assert P.role_counts(fx("person.html"), "director")["director"] == 18


def test_journal():
    items = P.journal_items(fx("journal_rss.html"))
    assert items[0]["title"] and items[0]["published_at"].endswith("Z") and items[0]["link"]


def test_autocomplete_json():
    data = json.loads(fx("autocomplete.json"))
    card = P.film_json(data["data"][0])
    assert card["slug"] == "parasite-2019" and card["directors"] == ["Bong Joon Ho"]
