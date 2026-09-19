"""/letterboxd/people/details — a cast / crew member or studio: bio,
photo, TMDB id, every role they hold (with film counts) and their full
filmography for one role (Letterboxd lists it on a single page)."""
from letterboxd import fetch
from letterboxd import parsers as P
from letterboxd import refs
from letterboxd.shared import finalize, path


def _resolve_role(slug):
    """A bare person slug -> the role with the most films (the actor page
    of anyone carries the full role menu)."""
    markup = fetch.html(f"/actor/{slug}/", label=f"person {slug}",
                        missing=f"person '{slug}' not found on Letterboxd")
    counts = {k: v for k, v in P.role_counts(markup, "actor").items() if k in refs.ROLES}
    if not counts:
        # studios have no role menu and an empty /actor/<slug>/ page
        if not P.grid_films(P.soup(markup)):
            return "studio", None
        return "actor", markup
    best = max(counts, key=lambda k: counts[k] or 0)
    # cameo / documentary appearances inflate "actor" for filmmakers: a
    # director with at least a third of the top count is a director first
    if (counts.get("director") or 0) * 3 >= (counts[best] or 0) and counts.get("director"):
        best = "director"
    return best, (markup if best == "actor" else None)


def details(person, role=None, sort="popular", genres=None):
    role = role or person.get("role")
    slug = person["slug"]
    markup = None
    if role is None:
        role, markup = _resolve_role(slug)
    if markup is None or sort != "popular" or genres:
        genre = f"genre/{refs.genre_segment(genres)}" if genres else None
        markup = fetch.html(path(role, slug, genre, refs.PERSON_SORTS[sort]), label=f"person {slug}",
                            missing=f"no {refs.ROLES.get(role, role).lower()} named '{slug}' on Letterboxd")
    out = P.person_page(markup, role, slug)
    out["sort"] = sort
    return finalize(out)
