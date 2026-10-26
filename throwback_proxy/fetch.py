"""Upstream HTTP fetching."""

import time

from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import UPSTREAM_UA


def http_get(url, timeout=10):
    """GET a URL; returns (status, headers-dict, body-bytes) or (None, None, None).

    *timeout* bounds BOTH the connect and the total body read.  The socket
    timeout that urlopen() applies only limits the gap between bytes, so a
    server that dribbles data (or holds the connection open without a
    Content-Length) would block resp.read() indefinitely and pin a handler
    thread forever.  We therefore read the body in chunks against a wall-clock
    deadline and give up if the upstream is too slow.
    """
    req = Request(
        url,
        headers={
            "User-Agent": UPSTREAM_UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    deadline = time.monotonic() + timeout
    try:
        resp = urlopen(req, timeout=timeout)
    except HTTPError as e:
        return e.code, {k.title(): v for k, v in (e.headers.items() or [])}, e.read()
    except Exception:
        return None, None, None
    # Normalise header keys to title case: some servers (Wikipedia) send
    # lowercase keys, and the caller looks them up as "Content-Type".
    headers = {k.title(): v for k, v in resp.headers.items()}
    chunks = []
    try:
        while True:
            if time.monotonic() > deadline:
                return None, None, None
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
