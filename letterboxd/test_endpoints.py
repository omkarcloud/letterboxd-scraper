"""Live endpoint smoke tests: one call per /letterboxd/* route against a
running service, with example values proven to return data (2026-09-19).
The listing tooling (derive_facts.py) reads each route's FIRST call from
this file's AST as its working example, so the values stay literals.

Skipped unless LETTERBOXD_BASE points at a running service:

    ONLY_SCRAPER=letterboxd python run.py            # or any bottle runner
    LETTERBOXD_BASE=http://127.0.0.1:6002 python -m pytest letterboxd/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("LETTERBOXD_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set LETTERBOXD_BASE to run live endpoint tests")


def call(path, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=180)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def test_search():
    assert call("/letterboxd/search", query="parasite")["results"][0]["poster"]["link"]
    assert call("/letterboxd/films/autocomplete", query="the godfather")["films"][0]["slug"] == "the-godfather"
    page1 = call("/letterboxd/search", query="love", type="lists")
    assert page1["next_cursor"] and call("/letterboxd/search", query="love", type="lists",
                                         cursor=page1["next_cursor"])["results"]
    assert call("/letterboxd/search", query="parasite", type="reviews")["results"][0]["text"]
    assert call("/letterboxd/search", query="bong joon", type="cast-crew")["results"][0]["role"]
    assert call("/letterboxd/search", query="dave", type="members")["results"][0]["username"]
    assert call("/letterboxd/search", query="horror", type="tags")["results"]


def test_films():
    film = call("/letterboxd/films/details", film="parasite-2019")
    assert film["cast"][0]["character"] and film["rating"]["distribution"] and film["stats"]["watches"]
    assert call("/letterboxd/films/stats", film="tt6751668")["rating"]["average"]
    assert call("/letterboxd/films/availability", film="parasite-2019", country="US")["rent"]
    assert call("/letterboxd/films/reviews", film="parasite-2019")["reviews"][0]["member"]["username"]
    assert call("/letterboxd/films/lists", film="parasite-2019")["lists"]
    assert call("/letterboxd/films/members", film="parasite-2019", type="fans")["members"]
    assert call("/letterboxd/films/similar", film="https://boxd.it/hTha")["films"]
    assert call("/letterboxd/films/themes", film="parasite-2019")["themes"][0]["films"]
    assert call("/letterboxd/films/poster", film="tmdb:496243", width=1000)["large_link"]
    assert call("/letterboxd/films/reviews", film="parasite-2019", sort="newest", rating=4)["reviews"]


def test_browse():
    assert call("/letterboxd/films/browse", sort="popular-week")["films"][0]["average_rating"]
    assert call("/letterboxd/films/browse", genres="horror,-comedy", decade="2020s", country="US")["films"]
    assert call("/letterboxd/films/browse", sort="rating", language="ko")["films"]
    assert call("/letterboxd/films/browse", theme="moving-relationship-stories")["films"]
    assert call("/letterboxd/films/collection", collection="toy-story-collection")["collection"]["name"]
    assert call("/letterboxd/tags", tag="horror")["films"]
    assert call("/letterboxd/lists/popular", period="week")["lists"]
    assert call("/letterboxd/members/popular")["members"][0]["films_count"]
    assert call("/letterboxd/reviews/popular")["reviews"]


def test_lists_reviews():
    lst = call("/letterboxd/lists/details", list="dave/imdb-top-250")
    assert lst["list"]["films_count"] == 250 and lst["entries"][0]["position"] == 1
    assert call("/letterboxd/lists/comments", list="https://boxd.it/7jLA")["comments"]
    review = call("/letterboxd/reviews/details", review="philbertdy/parasite-2019")
    assert review["text"] and review["rating"] == 5.0
    assert call("/letterboxd/reviews/comments", review="https://boxd.it/MV5qJ")["comments"]


def test_members():
    assert call("/letterboxd/members/details", member="dave")["stats"]["films"]
    assert call("/letterboxd/members/films", member="dave")["films"]
    assert call("/letterboxd/members/diary", member="dave", year=2024)["entries"]
    assert call("/letterboxd/members/reviews", member="dave")["reviews"]
    assert call("/letterboxd/members/watchlist", member="dave")["films"]
    assert call("/letterboxd/members/lists", member="dave")["lists"]
    assert call("/letterboxd/members/likes", member="dave", type="reviews")["reviews"]
    assert call("/letterboxd/members/followers", member="dave")["members"]
    assert call("/letterboxd/members/following", member="dave")["members"]
    assert call("/letterboxd/members/activity", member="dave")["activity"]
    assert call("/letterboxd/members/stats", member="dave")["summary"]["films"]
    assert call("/letterboxd/members/tags", member="dave")["tags"]


def test_people_journal():
    person = call("/letterboxd/people/details", person="bong-joon-ho")
    assert person["role"] == "director" and person["films"]
    assert call("/letterboxd/people/details", person="https://letterboxd.com/actor/song-kang-ho/")["films"]
    assert call("/letterboxd/journal")["articles"]
