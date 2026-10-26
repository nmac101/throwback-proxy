"""Proxy URL scheme:  /<scheme>/<host[:port]>/<path>?<query>
(the legacy "/p/<scheme>/..." prefix is also accepted)."""

import re
from urllib.parse import unquote, urlparse

def to_proxy(url, abs_base=None):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return url
    # IE6 has no TLS support: downgrade every https URL to http so the
    # links and resources actually load (upstream fetches are downgraded
    # the same way, so the host serves the http variant).
    if p.scheme == "https":
        p = p._replace(scheme="http")
    # System-proxy mode (abs_base set): the browser is configured to send
    # every http request to this proxy in absolute form, so links can keep
    # their original URL -- no /<scheme>/<host>/... wrapping needed.
    if abs_base:
        return p.geturl()
    path = p.path or "/"
    query = ("?" + p.query) if p.query else ""
    return "/%s/%s%s%s" % (p.scheme, p.netloc, path, query)


def from_proxy(path):
    m = re.match(r"^/(?:p/)?(http|https)(?:://|/)([^/]+)(/.*)?$", path)
    if not m:
        return None
    scheme, host, rest = m.group(1), m.group(2), (m.group(3) or "/")
    if "?" in rest:
        pth, query = rest.split("?", 1)
    else:
        pth, query = rest, ""
    url = "%s://%s%s" % (scheme, host, unquote(pth))
    if query:
        url += "?" + query
    return url


