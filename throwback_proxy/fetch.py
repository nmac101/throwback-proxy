"""Upstream HTTP fetching."""

from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import UPSTREAM_UA

def http_get(url, timeout=10):
    """GET a URL; returns (status, headers-dict, body-bytes) or (None, None, None)."""
    req = Request(
        url,
        headers={
            "User-Agent": UPSTREAM_UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    try:
        resp = urlopen(req, timeout=timeout)
    except HTTPError as e:
        return e.code, {k.title(): v for k, v in (e.headers.items() or [])}, e.read()
    except Exception:
        return None, None, None
    # Normalise header keys to title case: some servers (Wikipedia) send
    # lowercase keys, and the caller looks them up as "Content-Type".
    return resp.status, {k.title(): v for k, v in resp.headers.items()}, resp.read()

