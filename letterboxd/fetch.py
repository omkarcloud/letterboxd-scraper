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
  3. mint ONE identity with a headed patchright Chrome on the current
     egress, seeded on the challenged path (boomplay pattern: real mouse
     click on the Turnstile checkbox; validated 2026-09-19 on a challenged
     residential US exit from a Mac — 2 clicks, 44 s), wait for
     cf_clearance and replay its cookies + user-agent over curl_cffi. A
     failed harvest backs off for config.LETTERBOXD_HARVEST_COOLDOWN
     seconds so Chrome is not relaunched on every request.
config.LETTERBOXD_CF_FALLBACK turns step 3 off.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  LetterboxdUpstreamError  transport failure / 5xx            — retried
  LetterboxdBlocked        challenge that survived a fresh identity
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
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

from letterboxd import refs

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
HARVEST_CLEAR_TIMEOUT = 75
HARVEST_POLL = 1.0
CLICK_EVERY = 15
MAX_CLICKS = 3

HTML_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
}
AJAX_HEADERS = {
    "accept": "application/json, text/javascript, text/html, */*; q=0.01",
    "accept-language": "en-US,en;q=0.9",
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
# ONE egress for the whole process (direct by default): a Cloudflare
# identity is bound to the IP it was minted on, so the harvest browser and
# every replaying curl session must share it. rotate_egress() picks a new
# sticky exit (only meaningful with config.LETTERBOXD_PROXY_COUNTRY) and
# bumps the version so thread-local sessions rebuild.
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
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


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
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def dump_debug(name, text):
    """Write a raw response to $LETTERBOXD_DEBUG_DIR/<name>.html."""
    dbg = os.environ.get("LETTERBOXD_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".html"), "w") as f:
                f.write(text)
        except OSError:
            pass


def looks_blocked(resp):
    """A Cloudflare interstitial rather than a real page / 404."""
    if any(k.lower() == "cf-mitigated" for k in resp.headers.keys()):
        return True
    if resp.status_code not in (403, 429, 503):
        return False
    low = (resp.text or "")[:6000].lower()
    return any(m in low for m in CF_MARKERS)


# ---- Cloudflare identity (fallback) ------------------------------------------------

_identity = None           # {"cookies": dict, "ua": str, "egress": version}
_harvest_failed_until = 0.0
_identity_lock = threading.Lock()


def _click_turnstile(page):
    """Real mouse click on the Turnstile checkbox: the widget iframe's
    bounding box, x at 1/9 of the width (the checkbox), y at the middle.
    Returns True when a challenge frame was found and clicked."""
    for frame in page.frames:
        if not frame.url.startswith("https://challenges.cloudflare.com"):
            continue
        try:
            box = frame.frame_element().bounding_box()
        except Exception:
            box = None
        if not box or box.get("width", 0) < 50:
            continue
        x = box["x"] + box["width"] / 9 + random.uniform(-5, 5)
        y = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
        page.mouse.move(x, y, steps=random.randint(10, 25))
        page.wait_for_timeout(random.randint(100, 300))
        page.mouse.down()
        page.wait_for_timeout(random.randint(50, 150))
        page.mouse.up()
        return True
    return False


def _cf_cookie(driver):
    cookies = driver.call(lambda page: page.context.cookies(SITE))
    return any(c.get("name") == "cf_clearance" for c in cookies), cookies


def _harvest_identity(seed_path):
    """One throwaway headed patchright Chrome loads the path Cloudflare
    challenged (challenges are PATH-scoped: /films/popular/ can be open
    while /<member>/ and /csi/… are challenged on the same exit) and waits
    for a cf_clearance cookie; returns {cookies, ua}, validated over curl
    on the same path and egress (cf_clearance is bound to IP + UA)."""
    from curl_cffi import requests as curl_requests
    from chrome_manager import create_scope
    from patchright_driver import PatchrightDriver

    proxy, _ = egress()
    print(f"letterboxd: harvesting a Cloudflare identity on {seed_path} via patchright "
          f"(proxy={'direct' if not proxy else proxy.rsplit('@', 1)[-1]})...")
    with create_scope():
        driver = PatchrightDriver(proxy_url=proxy, headless=False)
    try:
        try:
            driver.goto(SITE + seed_path, referer="https://www.google.com/")
        except Exception as e:
            raise RuntimeError(f"seed navigation failed: {e}")
        start = time.monotonic()
        clicks, last_click = 0, None
        while True:
            cleared, cookies = _cf_cookie(driver)
            title = (driver.title() or "").lower()
            if cleared and "just a moment" not in title:
                break
            elapsed = time.monotonic() - start
            if elapsed > HARVEST_CLEAR_TIMEOUT:
                raise RuntimeError(f"Cloudflare challenge did not clear (clicks={clicks})")
            due = (last_click is None and elapsed >= 3) or \
                  (last_click is not None and time.monotonic() - last_click >= CLICK_EVERY)
            if due and clicks < MAX_CLICKS and driver.call(_click_turnstile):
                clicks += 1
                last_click = time.monotonic()
            time.sleep(HARVEST_POLL)
        ua = driver.evaluate("navigator.userAgent")
        cookies = {c["name"]: c["value"] for c in cookies}
    finally:
        driver.close()
    with curl_requests.Session(impersonate=IMPERSONATE) as sess:
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        resp = sess.get(SITE + seed_path, headers={**HTML_HEADERS, "user-agent": ua},
                        cookies=cookies, timeout=TIMEOUT)
        if resp.status_code >= 500 or looks_blocked(resp):
            raise RuntimeError(f"identity replay validation failed: HTTP {resp.status_code}")
    print(f"letterboxd: Cloudflare identity ready ({len(cookies)} cookies, clicks={clicks}, "
          f"{time.monotonic() - start:.0f}s)")
    return {"cookies": cookies, "ua": ua}


def _get_identity(seed_path):
    global _identity, _harvest_failed_until
    with _identity_lock:
        if _identity is not None and _identity.get("egress") == egress()[1]:
            return _identity
        if time.time() < _harvest_failed_until:
            raise LetterboxdBlocked("Cloudflare challenge; a recent identity harvest failed, "
                                    "retrying after the cooldown")
        last = None
        for attempt in range(1, config.LETTERBOXD_HARVEST_ATTEMPTS + 1):
            try:
                _identity = _harvest_identity(seed_path)
                _identity["egress"] = egress()[1]
                return _identity
            except Exception as e:
                last = e
                print(f"letterboxd: harvest attempt {attempt}/{config.LETTERBOXD_HARVEST_ATTEMPTS} failed: {e}")
                if _proxied():
                    rotate_egress()         # a fresh exit for the next attempt (and the replays)
        # a failing harvest must not relaunch Chrome on every request
        _harvest_failed_until = time.time() + config.LETTERBOXD_HARVEST_COOLDOWN
        raise LetterboxdBlocked(f"Cloudflare challenge; identity harvest failed ({last})")


def _invalidate_identity(stale):
    global _identity
    with _identity_lock:
        if _identity is stale:
            _identity = None


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


def _once(path, params, ajax, identity, allow_redirects, direct=False):
    """One GET -> response, or None when Cloudflare challenged it."""
    headers = dict(AJAX_HEADERS if ajax else HTML_HEADERS)
    cookies = None
    if identity:
        headers["user-agent"] = identity["ua"]
        cookies = identity["cookies"]
    proxy, version = (None, None) if direct else egress()
    try:
        sess = _direct_session() if direct else _session()
        resp = sess.get(_url(path), params=params, headers=headers, cookies=cookies,
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
      1. as-is (with the current identity if one is minted for this exit);
      2. proxied: a fresh residential exit per challenge, up to
         LETTERBOXD_ROTATE_ATTEMPTS times — Cloudflare scores exits by IP
         reputation, and most US residential exits pass every path;
      3. a patchright-minted identity on the current egress, once."""
    if _proxied():
        cls = path_class(path)
        if _direct_ok(cls):
            try:
                resp = _once(path, params, ajax, None, allow_redirects, direct=True)
            except LetterboxdUpstreamError:
                resp = None
            else:
                if resp is not None:
                    return resp
                _block_direct(cls)
    rotations = 0
    while True:
        _, version = egress()
        identity = _identity if _identity is not None and _identity.get("egress") == version else None
        resp = _once(path, params, ajax, identity, allow_redirects)
        if resp is not None:
            return resp
        if _proxied() and rotations < config.LETTERBOXD_ROTATE_ATTEMPTS:
            rotations += 1
            rotate_egress(version)
            continue
        break
    if not config.LETTERBOXD_CF_FALLBACK:
        raise LetterboxdBlocked(f"Cloudflare challenge on {path}")
    if identity is not None:
        _invalidate_identity(identity)
    seed = urlparse(_url(path)).path or "/"
    identity = _get_identity(seed)
    resp = _once(path, params, ajax, identity, allow_redirects)
    if resp is not None:
        return resp
    _invalidate_identity(identity)
    raise LetterboxdBlocked(f"Cloudflare challenge on {path} even with a fresh identity")


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
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(workers, len(fns))) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


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
