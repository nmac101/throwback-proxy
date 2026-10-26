"""Ad / tracking domain matching (host suffixes)."""

from urllib.parse import urlparse

from .config import AD_DOMAINS

def _is_ad_url(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in AD_DOMAINS)

