#!/usr/bin/env python3
"""
ie6proxy.py -- an HTTP proxy that rewrites modern web pages so they can be
rendered by Internet Explorer 6, while keeping the original structure, and
blocks ads along the way.

Usage:
    python3 ie6proxy.py [port]          (default port: 8080)

Then point IE6 at:
    http://127.0.0.1:8080/              (landing page with a URL box)
or browse directly to:
    http://127.0.0.1:8080/p/http/example.com/page.html

How it works:
  * Every response is fetched upstream and, depending on its type:
      - text/html : HTML5 tags are down-converted (section->div, ...),
                    <canvas>/<video>/<audio> are removed, ES-module scripts
                    are dropped, ad scripts are stripped, and every
                    src/href/poster/style-url() is rewritten to go through
                    this proxy.
      - text/css  : @media/@supports/@font-face/keyframes are removed,
                    flexbox/grid become block/inline, position:fixed becomes
                    absolute, gradients collapse to a solid color,
                    rgba()/hsl() become rgb(), calc()/vw/vh are approximated,
                    unsupported properties and selectors are dropped, and
                    url() references are rewritten through the proxy.
      - image/webp: converted to PNG on the fly (if Pillow is installed).
  * Requests whose host matches a known ad/tracking domain are served an
    empty response (204) or a transparent 1x1 GIF, without touching the
    network at all.

Only the Python standard library is required. Pillow is optional (webp).
"""

import colorsys
import io
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

try:  # optional: webp -> png conversion
    from PIL import Image

    def _webp_to_png(data):
        img = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
except ImportError:  # pragma: no cover
    _webp_to_png = None

try:
    from http.server import ThreadingHTTPServer  # py>=3.7
except ImportError:  # pragma: no cover
    from socketserver import ThreadingMixIn
    from http.server import HTTPServer

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        pass


HOST = "127.0.0.1"
DEFAULT_PORT = 8080
UPSTREAM_UA = (
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
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


# ---------------------------------------------------------------------------
# Proxy URL scheme:  /p/<scheme>/<host[:port]>/<path>?<query>
# ---------------------------------------------------------------------------
def to_proxy(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return url
    path = p.path or "/"
    query = ("?" + p.query) if p.query else ""
    return "/p/%s/%s%s%s" % (p.scheme, p.netloc, path, query)


def from_proxy(path):
    m = re.match(r"^/p/(http|https)/([^/]+)(/.*)?$", path)
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


# ---------------------------------------------------------------------------
# Small balanced-delimiter helpers
# ---------------------------------------------------------------------------
def _balanced_paren(s, i):
    """s[i] == '(' -> index of matching ')'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(s) - 1


def _balanced_brace(s, i):
    """s[i] == '{' -> index of matching '}'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(s) - 1


# ---------------------------------------------------------------------------
# CSS rewriting
# ---------------------------------------------------------------------------
BAD_PROP_RE = re.compile(
    r"""^(?:
      border-radius | (?:border-)?(top|bottom)-(left|right)-radius |
      box-shadow | text-shadow |
      transform(-origin)? | transition(-property|-delay|-duration|-timing-function)? |
      animation(-name|-duration|-delay|-iteration-count|-direction|-fill-mode|-play-state)? |
      -webkit-(?:transform|transition|animation|appearance|user-select|border-radius|box-shadow)(?:-.*)? |
      user-select | backface-visibility | perspective(-origin)? |
      overflow-x | overflow-y | will-change | scroll-behavior |
      gap | grid-template(-rows|-columns|-areas)? |
      grid-(?:row|column|area|gap|auto-flow) | place-items | place-content |
      object-fit | mix-blend-mode | filter | backdrop-filter | caret-color | resize |
      appearance | justify-content | align-items | align-content | align-self |
      flex(-(?:grow|shrink|basis|flow|wrap))? | order | aspect-ratio |
      content-visibility | mask(-image|-size|-repeat)? | clip-path | contain |
      text-decoration-(?:style|thickness) | line-clamp | -webkit-line-clamp |
      overscroll-behavior
    )$""",
    re.I | re.X,
)

BAD_SELECTOR_RE = re.compile(
    r":not\(|:nth-|:is\(|:where\(|:has\(|:focus-within|:fullscreen"
)


def _strip_at_blocks(css, names):
    pat = re.compile(r"@(?:" + "|".join(names) + r")\b[^{]*\{", re.I)
    while True:
        m = pat.search(css)
        if not m:
            return css
        j = _balanced_brace(css, m.end() - 1)
        css = css[: m.start()] + " " + css[j + 1 :]


def _replace_gradients(css):
    for name in (
        "linear-gradient",
        "radial-gradient",
        "-webkit-linear-gradient",
        "-moz-linear-gradient",
    ):
        while True:
            i = css.lower().find(name + "(")
            if i < 0:
                break
            j = _balanced_paren(css, i + len(name))
            inner = css[i + len(name) + 1 : j]
            cm = re.search(
                r"(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\))", inner
            )
            color = cm.group(1) if cm else "#ffffff"
            css = css[:i] + color + css[j + 1 :]
    return css


def _eval_calc(inner):
    s = inner.strip()
    if re.fullmatch(r"[\d\s.+\-*/()]+", s):
        try:
            return "%gpx" % eval(s, {"__builtins__": {}}, {})
        except Exception:
            pass
    if "%" in s and re.fullmatch(r"[\d\s.%+\-*/()]+", s):
        try:
            return "%g%%" % eval(s, {"__builtins__": {}}, {})
        except Exception:
            pass
    return inner  # leave it; IE6 will simply ignore the declaration


def _rewrite_css_urls(css, base):
    def sub(m):
        inner = m.group(2).strip().strip("'\''")
        if not inner or inner.lower().startswith(("data:", "#")):
            return m.group(0)
        absu = urljoin(base, inner)
        return 'url("%s")' % to_proxy(absu)

    return re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", sub, css)


def _filter_rules(css):
    def sub(m):
        sel, body = m.group(1), m.group(2)
        sel = sel.replace("::", ":")  # IE6 uses single-colon pseudos
        if BAD_SELECTOR_RE.search(sel):
            return ""
        decls = []
        for d in body.split(";"):
            if ":" not in d:
                continue
            prop, _, val = d.partition(":")
            p = prop.strip().lower()
            if BAD_PROP_RE.match(p):
                continue
            v = val.strip()
            if p == "display" and re.search(r"(inline-)?(flex|grid)", v):
                v = "inline" if "inline" in v else "block"
            elif p == "position" and v.lower().startswith("fixed"):
                v = "absolute"
            decls.append("%s: %s" % (prop.strip(), v))
        return "%s { %s }" % (sel, "; ".join(decls))

    return re.sub(r"([^{}]+)\{([^{}]*)\}", sub, css)


def transform_css(css, base):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = _strip_at_blocks(
        css, ["font-face", "keyframes", "-webkit-keyframes", "-moz-keyframes"]
    )
    css = _strip_at_blocks(css, ["media", "supports"])
    css = _replace_gradients(css)
    css = re.sub(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)[^)]*\)", r"rgb(\1,\2,\3)", css, re.I
    )

    def _hsl(m):
        h, s, l = (
            float(m.group(1)) / 360.0,
            float(m.group(2)) / 100.0,
            float(m.group(3)) / 100.0,
        )
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return "rgb(%d,%d,%d)" % (round(r * 255), round(g * 255), round(b * 255))

    css = re.sub(
        r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%", _hsl, css
    )
    css = re.sub(r"calc\(([^)]*)\)", lambda m: _eval_calc(m.group(1)), css)
    # IE6-era screens: assume ~1024x768 for vw/vh approximations
    css = re.sub(
        r"([\d.]+)\s*vw",
        lambda m: "%dpx" % round(float(m.group(1)) * 10.24),
        css,
    )
    css = re.sub(
        r"([\d.]+)\s*vh",
        lambda m: "%dpx" % round(float(m.group(1)) * 7.68),
        css,
    )
    css = _rewrite_css_urls(css, base)
    css = _filter_rules(css)
    return css


# ---------------------------------------------------------------------------
# HTML rewriting
# ---------------------------------------------------------------------------
TAG_MAP = {
    "section": "div",
    "article": "div",
    "nav": "div",
    "header": "div",
    "footer": "div",
    "main": "div",
    "aside": "div",
    "figure": "div",
    "figcaption": "div",
    "details": "div",
    "time": "span",
    "bdi": "span",
    "bdo": "span",
    "output": "span",
    "mark": "span",
    "summary": "span",
}

ATTR_RE = re.compile(
    r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"""
)

SKIP_URL_PREFIXES = ("data:", "#", "javascript:", "mailto:", "tel:", "about:")


def _rewrite_attrs(tag, base):
    def sub(a):
        name = a.group(1).lower()
        val = next(g for g in a.groups()[1:] if g is not None)
        if name == "srcset":
            return ""
        if name == "style":
            return 'style="%s"' % _rewrite_css_urls(val, base).replace(
                '"', "'"
            )
        if name in ("src", "href", "poster") and val:
            v = val.strip()
            if v.lower().startswith(SKIP_URL_PREFIXES):
                return a.group(0)
            absu = urljoin(base, v)
            if urlparse(absu).scheme in ("http", "https"):
                v = to_proxy(absu)
            return '%s="%s"' % (a.group(1), v)
        return a.group(0)

    return ATTR_RE.sub(sub, tag)


def transform_html(html, base):
    # <base href> changes how relative URLs resolve -- honour it first.
    bm = re.search(r"<base[^>]+href\s*=\s*['\"]([^'\"]+)['\"]", html, re.I)
    if bm:
        base = urljoin(base, bm.group(1))

    # Drop ES modules and ad scripts; strip defer/async from the rest.
    html = re.sub(
        r"<script[^>]*type\s*=\s*['\"]?module['\"]?[^>]*>.*?</script\s*>",
        "",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r"<script[^>]*(?:adsbygoogle|googlesyndication)[^>]*>.*?</script\s*>",
        "",
        html,
        flags=re.I | re.S,
    )

    def _clean_script_open(m):
        return re.sub(r"\s(?:defer|async)\b", "", m.group(0))

    html = re.sub(r"<script\b[^>]*>", _clean_script_open, html, flags=re.I)

    # <picture>: keep the fallback <img>, drop <source>s.
    html = re.sub(r"<picture(\s[^>]*)?>", r"<div\1>", html, flags=re.I)
    html = re.sub(r"</picture\s*>", "</div>", html, flags=re.I)
    html = re.sub(r"<source\b[^>]*>", "", html, flags=re.I)

    # Media elements IE6 cannot show.
    html = re.sub(
        r"<(video|audio|canvas)(\s[^>]*)?>.*?</\1\s*>",
        "<span></span>",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(r"<(video|audio|canvas)\b[^>]*/>", "", html, flags=re.I)

    # HTML5 element down-conversion.
    for old, new in TAG_MAP.items():
        html = re.sub(
            r"<%s(\s[^>]*)?>" % old, "<%s\\1>" % new, html, flags=re.I
        )
        html = re.sub(r"</%s\s*>" % old, "</%s>" % new, html, flags=re.I)
    html = re.sub(r"<wbr\s*/?>", "", html, flags=re.I)

    # Standards-mode doctype + explicit charset for IE6.
    html = re.sub(
        r"<!doctype[^>]*>", XHTML_STRICT_DOCTYPE, html, count=1, flags=re.I
    )
    if not re.search(r"<meta[^>]+charset", html, re.I):
        ins = '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
        if re.search(r"<head[^>]*>", html, re.I):
            html = re.sub(r"(<head[^>]*>)", r"\1" + ins, html, count=1, flags=re.I)
        elif re.search(r"<html[^>]*>", html, re.I):
            html = re.sub(r"(<html[^>]*>)", r"\1" + ins, html, count=1, flags=re.I)

    # Rewrite every tag's URL-bearing attributes through the proxy.
    html = re.sub(
        r"<[a-zA-Z][^>]*>", lambda m: _rewrite_attrs(m.group(0), base), html
    )
    return html


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
def _is_ad_url(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in AD_DOMAINS)


LANDING = """<!DOCTYPE html>
<html><head><title>IE6 Proxy</title></head>
<body bgcolor="#ffffff" text="#000000">
<center>
<h1>Internet Explorer 6 Proxy</h1>
<p>Enter a URL to browse it through the IE6-ifying proxy:</p>
<form onsubmit="if(this.u.value){var v=this.u.value;
if(v.indexOf('http')!==0){v='http://'+v;}
location.href='/p/'+v.substring(8);}return false;">
<input type="text" name="u" size="50" value="example.com">
<input type="submit" value="Go">
</form>
<p style="font-size:11px;color:#666666">
All pages are rewritten for IE6 (HTML4, no CSS3, no flexbox) and ads are blocked.
</p>
</center>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "IE6Proxy/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # -- helpers ------------------------------------------------------------
    def _send(self, body, ctype, status=200, head_only=False, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        if "charset" not in ctype and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- verbs --------------------------------------------------------------
    def do_GET(self):
        self._handle(head_only=False)

    def do_HEAD(self):
        self._handle(head_only=True)

    def do_POST(self):
        self._send("<html><body>POST is not proxied by IE6Proxy.</body></html>",
                   "text/html", 405)

    # -- main ---------------------------------------------------------------
    def _handle(self, head_only):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._send(LANDING, "text/html", head_only=head_only)
            return

        url = from_proxy(path)
        if not url:
            self._send("<html><body>Not found</body></html>", "text/html", 404,
                       head_only)
            return

        # --- ad blocking (no upstream request at all) ---
        if _is_ad_url(url):
            p = urlparse(url)
            if re.search(r"\.(gif|png|jpe?g|webp|svg)$", p.path, re.I):
                self._send(TRANSPARENT_GIF, "image/gif", 204 if head_only else 200,
                           head_only)
            else:
                self._send(b"", "text/plain", 204, head_only)
            return

        # --- fetch upstream ---
        req = Request(
            url,
            headers={
                "User-Agent": UPSTREAM_UA,
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            },
        )
        try:
            resp = urlopen(req, timeout=20)
        except HTTPError as e:
            body = e.read()
            if 300 <= e.code < 400 and e.headers.get("Location"):
                loc = urljoin(url, e.headers["Location"])
                self._redirect(to_proxy(loc))
                return
            self._send(body, e.headers.get_content_type() or "text/plain",
                       e.code, head_only)
            return
        except (URLError, OSError) as e:
            self._send(
                "<html><body>Upstream error: %s</body></html>" % e,
                "text/html", 502, head_only,
            )
            return

        body = resp.read()
        ctype = (resp.headers.get_content_type() or "application/octet-stream")
        charset_m = re.search(r"charset=([\w-]+)",
                              resp.headers.get("Content-Type") or "", re.I)
        charset = charset_m.group(1).lower() if charset_m else "utf-8"

        # --- transform by type ---
        if ctype == "text/html":
            text = body.decode(charset, errors="replace")
            text = transform_html(text, url)
            self._send(text, "text/html", 200, head_only)
            return
        if ctype == "text/css":
            text = body.decode(charset, errors="replace")
            text = transform_css(text, url)
            self._send(text, "text/css", 200, head_only)
            return
        if ctype == "image/webp" and _webp_to_png:
            body = _webp_to_png(body)
            ctype = "image/png"

        self._send(body, ctype, 200, head_only)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = ThreadingHTTPServer((HOST, port), Handler)
    print("IE6 proxy listening on http://%s:%d/" % (HOST, port))
    print("Try:  http://%s:%d/p/http/example.com/" % (HOST, port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
