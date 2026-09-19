"""/letterboxd/journal — the latest Letterboxd Journal articles (the
site's editorial magazine: interviews, lists, festival coverage), read
from its RSS feed (title, link, category, teaser, header image, date)."""
from letterboxd import fetch
from letterboxd import parsers as P


def latest():
    markup = fetch.html("/journal/rss/", label="journal")
    return {"articles": P.journal_items(markup)}
