"""The one piece of the hosted service's browser pool manager this project
needs: create_scope(), the lock held while a throwaway patchright Chrome
launches (letterboxd/fetch.py's Cloudflare clearance). There are no browser
pools here, so it only serializes launches."""
import threading
from contextlib import contextmanager

_create_lock = threading.Lock()


@contextmanager
def create_scope():
    """Hold the driver-construction lock around a throwaway browser launch."""
    with _create_lock:
        yield
