"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. The endpoint functions live in letterboxd/films.py,
search.py, members.py, lists.py, reviews.py, people.py and browse.py.
"""
import json
import os

from letterboxd import films, members, refs, search

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a slug, a letterboxd.com / boxd.it link, an IMDb id or tmdb:<id>
    save("film_interstellar.json", films.details(refs.resolve_film("interstellar")))

    # films, reviews, lists, cast-crew, members or tags — 20 per call
    save("search_dune.json", search.search("dune", type="films"))

    # a username or profile link
    save("member_kurstboy.json", members.details(refs.resolve_member("kurstboy")))
