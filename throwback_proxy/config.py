"""Global configuration constants for Throwback Proxy."""

HOST = "0.0.0.0"
DEFAULT_PORT = 8080
UPSTREAM_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"
)

# ---------------------------------------------------------------------------
# Ad / tracking domains (matched as host suffixes)
# ---------------------------------------------------------------------------
AD_DOMAINS = {
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.com",
    "google-analytics.com",
    "googletagmanager.com",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
    "criteo.com",
    "criteo.net",
    "pubmatic.com",
    "rubiconproject.com",
    "indexww.com",
    "moatads.com",
    "doubleverify.com",
    "adsafeprotected.com",
    "amazon-adsystem.com",
    "scorecardresearch.com",
    "quantserve.com",
    "adcolony.com",
    "mopub.com",
    "openx.net",
    "serving-sys.com",
}

TRANSPARENT_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c"
    "00000000010001000002024401003b"
)

XHTML_STRICT_DOCTYPE = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
    '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">'
)
