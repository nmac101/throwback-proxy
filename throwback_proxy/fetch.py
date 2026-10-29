"""Upstream HTTP fetching."""

import threading
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import UPSTREAM_UA


class _NoRedirect(HTTPRedirectHandler):
    """Surface 3xx responses (as HTTPError) instead of following them."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = build_opener(_NoRedirect)

MAX_REDIRECTS = 8


def _is_scheme_upgrade(src, dst):
    """True when *dst* is *src* with only the scheme changed (http->https).

    Sites that force HTTPS answer every http:// request with exactly this
    redirect.  The proxy talks https upstream and serves http downstream, so
    these are followed internally -- passing them on would send the browser
    into an endless http -> https -> (proxy downgrade) -> http loop.  Any
    other redirect (e.g. example.com -> www.example.com) is passed to the
    browser so relative URLs resolve against the host it actually lands on.
    """
    a, b = urlparse(src), urlparse(dst)
    return (a.scheme == "http" and b.scheme == "https"
            and a.netloc.lower() == b.netloc.lower().replace(":443", "")
            and a.path == b.path and a.query == b.query)


def _get_once(url, timeout, deadline):
    """One request, no redirects: (status, headers, body) or Nones."""
    req = Request(
        url,
        headers={
            "User-Agent": UPSTREAM_UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    try:
        resp = _opener.open(req, timeout=timeout)
    except HTTPError as e:
        # Error/redirect bodies need the same deadline and close handling
        # as successful responses; e.read() could otherwise block forever.
        resp = e
    except Exception:
        return None, None, None
    # Normalise header keys to title case: some servers (Wikipedia) send
    # lowercase keys, and the caller looks them up as "Content-Type".
    headers = {k.title(): v for k, v in resp.headers.items()}
    chunks = []
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, None, None
            sock = getattr(getattr(getattr(resp, "fp", None), "raw", None),
                           "_sock", None)
            if sock is not None:
                sock.settimeout(min(timeout, remaining))
            # read1() returns as soon as *some* bytes are available (up to
            # 65536), so the deadline check above runs often.  A plain
            # read(65536) would block until the full buffer is filled and the
            # deadline would be meaningless against a dribbling server.
            chunk = resp.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception:
        return None, None, None
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return resp.status, headers, b"".join(chunks)


def http_get(url, timeout=10):
    """GET a URL; returns (status, headers-dict, body-bytes) or (None, None, None).

    *timeout* bounds BOTH the connect and the total body read.  The socket
    timeout that urlopen() applies only limits the gap between bytes, so a
    server that dribbles data (or holds the connection open without a
    Content-Length) would block resp.read() indefinitely and pin a handler
    thread forever.  We therefore read the body in chunks against a wall-clock
    deadline and give up if the upstream is too slow.

    Scheme-only (http->https) redirects are followed; other redirects are
    returned as-is for the caller to relay.
    """
    deadline = time.monotonic() + timeout
    for _ in range(MAX_REDIRECTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, None, None
        status, headers, body = _get_once(url, remaining, deadline)
        loc = headers.get("Location") if headers else None
        if status in (301, 302, 303, 307, 308) and loc:
            nxt = urljoin(url, loc)
            if _is_scheme_upgrade(url, nxt):
                url = nxt
                continue
        return status, headers, body
    return None, None, None


# Stylesheets are fetched once to be scanned for layout hints and again by
# the browser; a short-lived cache avoids paying for the upstream twice.
_CACHE_TTL = 60
_CACHE_MAX = 64
_cache = {}
_cache_lock = threading.Lock()


def http_get_cached(url, timeout=10):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    res = http_get(url, timeout)
    if res[0] == 200:
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
            _cache[url] = (now, res)
    return res
