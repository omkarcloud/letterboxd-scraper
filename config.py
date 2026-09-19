"""Configuration for the Letterboxd Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT               port the API listens on (default 8000)
    LETTERBOXD_PROXY   proxy URL for every request, e.g. http://user:pass@host:port
                       (default: none — direct). letterboxd.com answered 216
                       parallel requests from one clean IP without a single
                       block, so you very likely don't need this. Residential
                       proxies are actually WORSE here: Cloudflare challenges
                       them on most pages. If your own IP is ever challenged,
                       the scraper clears Cloudflare once with a real Chrome
                       (patchright) and carries on over plain HTTP.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# Cloudflare fallback: clear a challenge once with a real Chrome, then replay
# its cookies over plain HTTP. Set to False to fail fast instead.
LETTERBOXD_CF_FALLBACK = True
LETTERBOXD_HARVEST_ATTEMPTS = 2
LETTERBOXD_HARVEST_COOLDOWN = 600      # seconds to wait after a failed clearance
LETTERBOXD_ROTATE_ATTEMPTS = 12        # only with a rotating proxy pool (see fetch.py)

LETTERBOXD_PROXY = os.environ.get("LETTERBOXD_PROXY") or None
# Only used to decide whether a failed Chrome attempt should pick a new exit.
LETTERBOXD_PROXY_COUNTRY = None


def letterboxd_proxy():
    """The proxy every request goes through (None = direct)."""
    return os.environ.get("LETTERBOXD_PROXY") or None
