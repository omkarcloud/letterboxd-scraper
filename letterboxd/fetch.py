"""Letterboxd transport: plain curl_cffi with browser-impersonated TLS
against letterboxd.com itself. No login, no cookies (validated 2026-09-19:
60 sequential + 216 parallel requests from one clean IP, all 200, no
throttling).

What the site serves anonymously, and where this scraper reads it:

  server-rendered pages   /film/<slug>/ (JSON-LD + cast/crew/details/genres/
                          releases tabs), /film/<slug>/{reviews,lists,members,
                          fans,likes,similar,themes}/, /<member>/{films,diary,
                          reviews,watchlist,lists,likes/*,followers,following,
                          stats,tags/*}/, /<member>/list/<slug>/[detail/],
                          /<role>/<person>/, /lists/popular/, /members/popular/,
                          /reviews/popular/, /tag/<tag>/*
  lazy-load fragments     /csi/films/films-browser-list/<filters>/ (browse grid,
                          72 per page), /csi/film/<slug>/{stats,rating-histogram,
                          popular-lists,recent-reviews}/, /csi/person/<m>/stats/*,
                          /csi/<list|viewing>/<id>/comments-section/,
                          /s/search/<type>/<q>/?cursor= (20 per call),
                          /ajax/<viewing|filmlist>:<id>/comments/?start=,
                          /ajax/activity-pagination/<m>/?after=
  JSON                    /film/<slug>/json/, /film/<slug>/poster/std/<w>/?k=,
                          /viewing/<id>/json/, /s/autocompletefilm,
                          /s/production-availabilities?productionUID=&locale=
  redirects               boxd.it/<code>, /imdb/<tt>/, /tmdb/<id>/ -> the page
  feeds                   /<member>/rss/, /journal/rss/

Page HTML no longer carries poster images for grids (a React component
resolves each one with a /poster/std/<w>/ JSON call), so posters() fans
those calls out in parallel and memoises them in-process.

Cloudflare fronts the site and scores callers by IP reputation, per path:
/film/<slug>/, /films/popular/ and feeds stay open, while /<member>/,
/csi/…, /s/…, /ajax/… and /film/<slug>/json/ get a managed challenge
(403 + `cf-mitigated: challenge`) on a poor-reputation IP. A home IP passes
everything; a cloud datacenter IP (the GKE pod) is challenged, and a headed
Chrome there does not clear it (verified 2026-09-19). Residential exits
vary — many pass every path, some are challenged. So the ladder is:
  1. the request on the current egress (config.LETTERBOXD_PROXY_COUNTRY
     sticky exit, or direct when None);
  2. proxied: rotate to a fresh exit per challenge, up to
     config.LETTERBOXD_ROTATE_ATTEMPTS;
and a challenge that survives the ladder raises LetterboxdBlocked.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  LetterboxdUpstreamError  transport failure / 5xx            — retried
  LetterboxdBlocked        challenge that survived every exit
  LetterboxdBadRequest     upstream rejected the params        — never retried
  LetterboxdNotFound       404 / private / unknown member etc. — never retried
"""
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

from letterboxd import refs
from scraper_utils import debug, fanout, sessions

SITE = refs.SITE
IMPERSONATE = "chrome"
TIMEOUT = 30
FANOUT_WORKERS = 12
POSTER_WIDTH = 500                 # url = 500x750, url2x = 1000x1500
POSTER_MEMO_TTL = 7 * 86400        # a film's poster almost never changes
POSTER_MEMO_MAX = 50000
RESOLVE_MEMO_TTL = 7 * 86400       # boxd.it / imdb / tmdb -> path never changes

CF_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "cf_chl_opt",
              "performing security verification", "attention required! | cloudflare")

HTML_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": egress.site_accept_language("letterboxd"),
}
AJAX_HEADERS = {
    "accept": "application/json, text/javascript, text/html, */*; q=0.01",
    "accept-language": egress.site_accept_language("letterboxd"),
    "x-requested-with": "XMLHttpRequest",
    "referer": SITE + "/",
}


class LetterboxdUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class LetterboxdBlocked(LetterboxdUpstreamError, Blocked):
    """Cloudflare challenge that a fresh identity could not clear."""


class LetterboxdBadRequest(BadRequest):
    """Upstream rejected the params — never retried."""


class LetterboxdNotFound(NotFound):
    """Unknown film / member / list / review — never retried."""


# ---- egress + sessions ---------------------------------------------------------------
# ONE egress for the whole process (direct by default). rotate_egress()
# picks a new sticky exit (only meaningful with
# config.LETTERBOXD_PROXY_COUNTRY) and bumps the version so thread-local
# sessions rebuild.
_egress_lock = threading.Lock()
_egress = {"proxy": None, "ready": False, "version": 0}
# Proxied exits that recently served a challenge-prone path (/csi/, /s/,
# /ajax/, a member page): a rotation reuses one of these before drawing a
# random new exit, since ~45% of random US exits are challenged somewhere.
_good_exits = []
GOOD_EXITS_MAX = 8


def egress():
    """(proxy URL | None, version)."""
    with _egress_lock:
        if not _egress["ready"]:
            if _good_exits:
                _egress["proxy"] = random.choice(_good_exits)
            else:
                _egress["proxy"] = config.letterboxd_proxy()
            _egress["ready"] = True
            _egress["version"] += 1
        return _egress["proxy"], _egress["version"]


def _mark_good(proxy):
    if not proxy:
        return
    with _egress_lock:
        if proxy not in _good_exits:
            _good_exits.append(proxy)
            del _good_exits[:-GOOD_EXITS_MAX]


def rotate_egress(seen_version=None):
    """Pick a new exit on the next request. With `seen_version`, only if no
    other thread rotated since that request started (one challenge must not
    set off a rotation storm across the worker threads)."""
    with _egress_lock:
        if seen_version is None or _egress["version"] == seen_version:
            if _egress["proxy"] in _good_exits:
                _good_exits.remove(_egress["proxy"])
            _egress["ready"] = False


def _proxied():
    return bool(config.LETTERBOXD_PROXY_COUNTRY)


# One curl session per worker thread (a curl handle must not be shared).
_local = threading.local()


def _direct_session():
    """This thread's proxy-less session (direct-first attempts)."""
    sess = getattr(_local, "direct", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        _local.direct = sess
    return sess


def _drop_direct_session():
    sess = getattr(_local, "direct", None)
    _local.direct = None
    sessions.close_quietly(sess)


def _session():
    proxy, version = egress()
    sess = getattr(_local, "session", None)
    if sess is not None and getattr(sess, "_lb_egress", None) != version:
        _drop_session()
        sess = None
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess._lb_egress = version
        _local.session = sess
    return sess


def _drop_session():
    sessions.drop(_local)


def dump_debug(name, text):
    """Write a raw response to $LETTERBOXD_DEBUG_DIR/<name>.html."""
    debug.dump("LETTERBOXD_DEBUG_DIR", name, text, ext=".html")


def looks_blocked(resp):
    """A Cloudflare interstitial rather than a real page / 404."""
    if any(k.lower() == "cf-mitigated" for k in resp.headers.keys()):
        return True
    if resp.status_code not in (403, 429, 503):
        return False
    low = (resp.text or "")[:6000].lower()
    return any(m in low for m in CF_MARKERS)


# ---- requests ---------------------------------------------------------------------

def _url(path):
    return path if path.startswith("http") else SITE + path


# ---- direct-first routing ------------------------------------------------------------
# Cloudflare challenges a poor-reputation IP per PATH CLASS: from the GKE pod
# /film/<slug>/, its poster JSON, /films/popular/, /lists/popular/ and the
# feeds answer 200 direct, while /film/<slug>/json/, /csi/, /s/, /ajax/ and
# member pages are challenged. With a proxy configured, each class is tried
# direct first; a challenge marks the class proxy-only for DIRECT_BLOCK_TTL
# seconds. The heaviest traffic (the per-film poster fan-out) then never
# touches a residential exit, which keeps the exits for the classes that
# need them and cuts rotations.
DIRECT_BLOCK_TTL = 6 * 3600
_direct_blocked = {}               # path class -> until (epoch seconds)
_direct_lock = threading.Lock()


def path_class(path):
    """'/film/x/json/' -> 'film/json'; '/film/x/' -> 'film/'; '/csi/…' ->
    'csi'; '/<member>/…' -> 'member'; absolute URLs -> their host."""
    parsed = urlparse(_url(path))
    if parsed.hostname not in ("letterboxd.com", "www.letterboxd.com"):
        return parsed.hostname or "?"
    segs = [x for x in parsed.path.split("/") if x]
    if not segs:
        return "/"
    head = segs[0]
    if head == "film":
        return "film/" + (segs[2] if len(segs) > 2 else "")
    if head in refs.RESERVED:
        return head
    return "member"


def _direct_ok(cls):
    with _direct_lock:
        return _direct_blocked.get(cls, 0) < time.time()


def _block_direct(cls):
    with _direct_lock:
        _direct_blocked[cls] = time.time() + DIRECT_BLOCK_TTL


def _once(path, params, ajax, allow_redirects, direct=False):
    """One GET -> response, or None when Cloudflare challenged it."""
    headers = dict(AJAX_HEADERS if ajax else HTML_HEADERS)
    proxy, version = (None, None) if direct else egress()
    try:
        sess = _direct_session() if direct else _session()
        resp = sess.get(_url(path), params=params, headers=headers,
                        timeout=TIMEOUT, allow_redirects=allow_redirects)
    except Exception as e:
        if direct:
            _drop_direct_session()
        else:
            _drop_session()
            if _proxied():
                rotate_egress(version)      # a dead / flaky exit: move on
        raise LetterboxdUpstreamError(f"request failed: {type(e).__name__}: {e}")
    if looks_blocked(resp):
        dump_debug("blocked", resp.text)
        return None
    if proxy and resp.status_code < 400 and not path.startswith("/film/"):
        _mark_good(proxy)                 # a path Cloudflare challenges on bad exits
    return resp


def _fetch(path, params, ajax, allow_redirects):
    """GET with the Cloudflare ladder:
      0. proxied config: direct first for path classes not yet seen
         challenged on this IP (see path_class);
      1. as-is on the current egress;
      2. proxied: a fresh residential exit per challenge, up to
         LETTERBOXD_ROTATE_ATTEMPTS times — Cloudflare scores exits by IP
         reputation, and most US residential exits pass every path."""
    if _proxied():
        cls = path_class(path)
        if _direct_ok(cls):
            try:
                resp = _once(path, params, ajax, allow_redirects, direct=True)
            except LetterboxdUpstreamError:
                resp = None
            else:
                if resp is not None:
                    return resp
                _block_direct(cls)
    rotations = 0
    while True:
        _, version = egress()
        resp = _once(path, params, ajax, allow_redirects)
        if resp is not None:
            return resp
        if _proxied() and rotations < config.LETTERBOXD_ROTATE_ATTEMPTS:
            rotations += 1
            rotate_egress(version)
            continue
        break
    raise LetterboxdBlocked(f"Cloudflare challenge on {path}")


def request(path, params=None, *, ajax=False, label=None, allow_redirects=True, missing=None):
    """GET a letterboxd.com path -> the curl response (2xx/3xx). 404 raises
    LetterboxdNotFound(missing or "<label> not found"); 5xx / transport
    errors retry with backoff; challenges go through _fetch's ladder."""
    label = label or path
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = _fetch(path, params, ajax, allow_redirects)
        except LetterboxdBlocked as e:
            if not _proxied():
                raise                       # direct egress: nothing new to try
            last = e                        # proxied: another round of fresh exits
        except LetterboxdUpstreamError as e:
            last = e
        else:
            if resp.status_code == 404:
                raise LetterboxdNotFound(missing or f"{label}: not found on Letterboxd")
            if resp.status_code in (400, 405):
                raise LetterboxdBadRequest(f"Letterboxd rejected {label} (HTTP {resp.status_code})")
            if resp.status_code < 400:
                return resp
            last = LetterboxdUpstreamError(f"HTTP {resp.status_code} on {label}")
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def html(path, params=None, **kwargs):
    """GET -> HTML text."""
    return request(path, params, **kwargs).text


def json_get(path, params=None, **kwargs):
    """GET -> parsed JSON (dict). A non-JSON body is an upstream error."""
    kwargs.setdefault("ajax", True)
    resp = request(path, params, **kwargs)
    try:
        return resp.json()
    except Exception:
        dump_debug("nonjson", resp.text)
        raise LetterboxdUpstreamError(f"{kwargs.get('label') or path} returned a non-JSON body")


# ---- parallel fan-out ---------------------------------------------------------------

def run_parallel(fns, workers=FANOUT_WORKERS):
    """Run zero-arg callables in parallel; results align with `fns`.
    Exceptions propagate from the first failing call."""
    return fanout.per_call(fns, workers)


def run_parallel_quiet(fns, workers=FANOUT_WORKERS):
    """run_parallel where a failing call yields None instead of raising
    (optional enrichment: posters, full review text)."""
    def safe(fn):
        def call():
            try:
                return fn()
            except (UpstreamError, NotFound, BadRequest, ValueError):
                return None
        return call
    return run_parallel([safe(fn) for fn in fns], workers)


# ---- memo -----------------------------------------------------------------------------

class _Memo:
    def __init__(self, ttl, max_size):
        self.ttl, self.max_size = ttl, max_size
        self.lock = threading.Lock()
        self.data = {}

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    def put(self, key, value):
        with self.lock:
            if len(self.data) >= self.max_size:
                self.data.clear()
            self.data[key] = (time.time() + self.ttl, value)


_posters = _Memo(POSTER_MEMO_TTL, POSTER_MEMO_MAX)
_resolved = _Memo(RESOLVE_MEMO_TTL, 20000)


# ---- posters ------------------------------------------------------------------------

def _poster(base_link, cache_key):
    try:
        data = json_get(f"{base_link}poster/std/{POSTER_WIDTH}/", {"k": cache_key}, label="poster")
    except LetterboxdNotFound:
        return {}
    if not isinstance(data, dict) or not data.get("url"):
        return {}
    return {"link": data.get("url"), "large_link": data.get("url2x")}


def posters(items):
    """[(uid, base_link "/film/<slug>/", cache key)] -> {uid: {link,
    large_link} | {}}; memoised, missing ones fetched in parallel. A film
    without artwork maps to {}."""
    out, todo = {}, []
    for uid, base, key in items:
        if not uid or not base or uid in out:
            continue
        hit = _posters.get(uid)
        if hit is not None:
            out[uid] = hit
        else:
            out[uid] = None
            todo.append((uid, base, key))
    results = run_parallel_quiet([lambda b=b, k=k: _poster(b, k) for _, b, k in todo])
    for (uid, _, _), poster in zip(todo, results):
        if poster is not None:
            _posters.put(uid, poster)
        out[uid] = poster or {}
    return out


# ---- reference resolution -----------------------------------------------------------

def redirect_path(url, what):
    """boxd.it / /imdb/ / /tmdb/ link -> the letterboxd.com path it redirects
    to (memoised). No redirect = unknown id."""
    hit = _resolved.get(url)
    if hit:
        return hit
    resp = request(url, allow_redirects=False, label=what, missing=f"{what} not found on Letterboxd")
    location = resp.headers.get("location") or resp.headers.get("Location")
    if resp.status_code not in (301, 302, 303, 307, 308) or not location:
        raise LetterboxdNotFound(f"{what} not found on Letterboxd")
    parsed = urlparse(location)
    if parsed.hostname and parsed.hostname not in ("letterboxd.com", "www.letterboxd.com"):
        raise LetterboxdNotFound(f"{what} does not lead to a Letterboxd page")
    path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
    _resolved.put(url, path)
    return path


def film_slug(ref):
    """A validated film ref -> its Letterboxd slug."""
    if ref.get("slug"):
        return ref["slug"]
    if ref.get("short_link"):
        url, what = ref["short_link"], f"short link {ref['short_link']}"
    elif ref.get("imdb_id"):
        url, what = f"{SITE}/imdb/{ref['imdb_id']}/", f"IMDb title {ref['imdb_id']}"
    else:
        url, what = f"{SITE}/tmdb/{ref['tmdb_id']}/", f"TMDB movie {ref['tmdb_id']}"
    segs = [s for s in redirect_path(url, what).split("/") if s]
    if len(segs) >= 2 and segs[0] == "film":
        return segs[1]
    raise LetterboxdBadRequest(f"{what} is not a film")


def list_path(ref):
    """A validated list ref -> "/<member>/list/<slug>/"."""
    if ref.get("short_link"):
        path = redirect_path(ref["short_link"], f"short link {ref['short_link']}")
        segs = [s for s in path.split("/") if s]
        if len(segs) >= 3 and segs[1] == "list":
            return f"/{segs[0]}/list/{segs[2]}/"
        raise LetterboxdBadRequest(f"{ref['short_link']} is not a list")
    return f"/{ref['username']}/list/{ref['slug']}/"


def review_path(ref):
    """A validated review ref -> "/<member>/film/<slug>/[<n>/]"."""
    if ref.get("short_link"):
        path = redirect_path(ref["short_link"], f"short link {ref['short_link']}")
        segs = [s for s in path.split("/") if s]
        if len(segs) >= 3 and segs[1] == "film":
            return "/" + "/".join(segs[:4]) + "/"
        raise LetterboxdBadRequest(f"{ref['short_link']} is not a review or diary entry")
    tail = f"{ref['entry']}/" if ref.get("entry") else ""
    return f"/{ref['username']}/film/{ref['film_slug']}/{tail}"


if __name__ == "__main__":
    # Smoke test: python letterboxd/fetch.py [path]
    target = sys.argv[1] if len(sys.argv) > 1 else "/film/parasite-2019/json/"
    print(json.dumps(json_get(target), ensure_ascii=False)[:500])
