"""Cache TTL per /letterboxd/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so `?page=1` and no `page` share a
row).

Tiers follow how fast each surface moves: a film's metadata, similar
films, themes and posters barely change; its counters (ratings, watches,
likes) tick constantly but a few hours of lag is fine; review and list
feeds, member diaries and activity move faster; popular / this-week charts
refresh through the day.
"""
from datetime import timedelta

# --- films ---------------------------------------------------------------------------
FILM_CACHE = timedelta(hours=12)           # details (metadata + counters + histogram)
FILM_STATS_CACHE = timedelta(hours=3)
AVAILABILITY_CACHE = timedelta(hours=12)
FILM_REVIEWS_CACHE = timedelta(hours=2)
FILM_LISTS_CACHE = timedelta(hours=6)
FILM_MEMBERS_CACHE = timedelta(hours=2)
SIMILAR_CACHE = timedelta(days=3)
THEMES_CACHE = timedelta(days=3)
POSTER_CACHE = timedelta(days=7)

# --- search / browse -------------------------------------------------------------------
SEARCH_CACHE = timedelta(hours=6)
AUTOCOMPLETE_CACHE = timedelta(days=1)
BROWSE_CACHE = timedelta(hours=6)
COLLECTION_CACHE = timedelta(days=1)
POPULAR_CACHE = timedelta(hours=1)
TAG_CACHE = timedelta(hours=6)

# --- lists / reviews ---------------------------------------------------------------------
LIST_CACHE = timedelta(hours=3)
REVIEW_CACHE = timedelta(hours=3)
COMMENTS_CACHE = timedelta(minutes=30)

# --- members -----------------------------------------------------------------------------
MEMBER_CACHE = timedelta(hours=3)
MEMBER_LIST_CACHE = timedelta(hours=1)     # films, diary, reviews, watchlist, lists, likes, people
ACTIVITY_CACHE = timedelta(minutes=10)
MEMBER_STATS_CACHE = timedelta(hours=12)
MEMBER_TAGS_CACHE = timedelta(hours=12)

# --- people / journal ---------------------------------------------------------------------
PERSON_CACHE = timedelta(days=1)
JOURNAL_CACHE = timedelta(hours=1)
