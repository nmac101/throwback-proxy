#!/usr/bin/env python3
"""
ie6proxy.py -- an HTTP proxy that rewrites modern web pages so they can be
rendered by Internet Explorer 6, while keeping the original structure, and
blocks ads along the way.

Usage:
    python3 ie6proxy.py [port]          (default port: 8080)

Then set it as your system/LAN proxy (this machine's IP, port 8080).
Absolute-form requests are proxied directly, and CONNECT tunnels for
HTTPS sites are terminated with a self-signed certificate and downgraded
to HTTP upstream. There are no direct proxy URLs -- the browser must use
it as its configured proxy.

How it works:
  * Every response is fetched upstream and, depending on its type:
      - text/html : HTML5 tags are down-converted (section->div, ...),
                    <canvas>/<video>/<audio> are removed, ES-module scripts
                    are dropped, ad scripts are stripped, and every
                    src/href/poster/style-url() is rewritten to go through
                    this proxy.
      - text/css  : @media/@supports/@font-face/keyframes are removed,
                    flexbox rows are approximated with inline-block children,
                    position:fixed becomes absolute, pinned by a JS
                    scroll handler (CSS expressions don't run in the
                    standards mode the proxy forces via the doctype),
                    gradients/shadows/opacity become DXImageTransform filters,
                    rgba()/hsl() become rgb(), calc()/vw/vh are approximated,
                    unsupported properties and selectors are dropped, and
                    url() references are rewritten through the proxy.
      - image/webp: converted to PNG on the fly (if Pillow is installed).
  * All upstream HTTPS requests are transparently downgraded to HTTP.
  * Requests whose host matches a known ad/tracking domain are served an
    empty response (204) or a transparent 1x1 GIF, without touching the
    network at all.

Only the Python standard library is required. Pillow is optional (webp);
the openssl CLI is optional (enables CONNECT/TLS for system-proxy mode).
"""

import colorsys
from html import unescape as html_unescape
import io
import os
import re
import ssl
import subprocess
import sys
import tempfile
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


HOST = "0.0.0.0"
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
# Proxy URL scheme:  /<scheme>/<host[:port]>/<path>?<query>
# (the legacy "/p/<scheme>/..." prefix is also accepted)
# ---------------------------------------------------------------------------
def to_proxy(url, abs_base=None):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return url
    # System-proxy mode (abs_base set): the browser is configured to send
    # every http request to this proxy in absolute form, so links can keep
    # their original URL -- no /<scheme>/<host>/... wrapping needed.
    if abs_base:
        return url
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


_NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "green": "#008000", "lime": "#00ff00", "blue": "#0000ff",
    "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0",
    "maroon": "#800000", "navy": "#000080", "teal": "#008080",
    "olive": "#808000", "aqua": "#00ffff", "cyan": "#00ffff",
    "magenta": "#ff00ff", "fuchsia": "#ff00ff",
}


def _parse_color(color):
    """Color -> (rrggbb, aa) lowercase hex pairs, or None."""
    c = color.strip().lower()
    if c in _NAMED_COLORS:
        return _NAMED_COLORS[c][1:], "ff"
    m = re.match(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)\s*(?:[,/]\s*([\d.]+%?)?)?\s*\)$", c)
    if m:
        rrggbb = "%02x%02x%02x" % tuple(min(255, int(x)) for x in m.groups()[:3])
        a = m.group(4)
        if a is None:
            aa = "ff"
        else:
            av = float(a.rstrip("%"))
            av = av / 100.0 if a.endswith("%") else av
            aa = "%02x" % round(av * 255)
        return rrggbb, aa
    m = re.fullmatch(r"#([0-9a-f]{6})([0-9a-f]{2})", c)  # #rrggbbaa
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        return c[1:], "ff"
    m = re.fullmatch(r"#([0-9a-f]{3})([0-9a-f])", c)  # #rgba
    if m:
        return "".join(ch * 2 for ch in m.group(1)), m.group(2) * 2
    if re.fullmatch(r"#[0-9a-f]{3}", c):
        return "".join(ch * 2 for ch in c[1:]), "ff"
    return None


def _to_hex(color):
    """Color -> #rrggbb (alpha dropped); for CSS properties."""
    p = _parse_color(color)
    return "#" + p[0] if p else None


def _to_argb(color):
    """Color -> #aarrggbb; the DXImageTransform filters (Gradient,
    DropShadow, Shadow) silently fail on 6-digit #rrggbb strings."""
    p = _parse_color(color)
    return "#" + p[1] + p[0] if p else None


def _px(value):
    m = re.match(r"\s*(-?[\d.]+)px", value)
    return float(m.group(1)) if m else 0.0


_COLOR_RE = r"(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|%s\b)" % "|".join(_NAMED_COLORS)


def _gradient_filter(value):
    """IE6 DX gradient filter approximating a CSS gradient.

    Returns (filter_value, first_color) or None. Only the two end colors and
    the nearest of the four cardinal directions can be represented.
    """
    matches = list(re.finditer(r"(?:linear|radial)-gradient\(", value))
    if not matches:
        return None
    # With multiple comma-separated background layers, CSS paints the first
    # listed one on top.  We can only emit one filter, so use the LAST
    # gradient (the opaque base layer) and ignore translucent overlays.
    m = matches[-1]
    i = value.index("(", m.start())
    j = _balanced_paren(value, i)
    inner = value[i + 1 : j]
    colors = re.findall(_COLOR_RE, inner)
    if not colors:
        return None
    first, last = colors[0], colors[-1]
    am = re.match(r"\s*(?:to\s+(right|left|top|bottom)|(-?[\d.]+)deg?)", inner)
    angle = 0.0  # CSS default direction is "to top"
    if am:
        if am.group(1):
            angle = {"top": 0, "right": 90, "bottom": 180, "left": 270}[am.group(1)]
        else:
            angle = float(am.group(2)) % 360
    else:
        # radial-gradient(... at <pos>): the color radiates from <pos>, so
        # the linear approximation points away from it.
        pm = re.search(
            r"\bat\s+((?:top|bottom)\s+(?:left|right)|(?:left|right)\s+"
            r"(?:top|bottom)|top|bottom|left|right)",
            inner, re.I,
        )
        if pm:
            pos = re.sub(r"\s+", " ", pm.group(1).lower())
            angle = {
                "top": 180, "bottom": 0, "left": 90, "right": 270,
                "top left": 225, "left top": 225,
                "top right": 45, "right top": 45,
                "bottom left": 135, "left bottom": 135,
                "bottom right": 315, "right bottom": 315,
            }[pos]
    # snap to nearest cardinal, circularly (350deg -> 0, not 270)
    angle = min(
        (0, 90, 180, 270),
        key=lambda a: min(abs(a - angle), 360 - abs(a - angle)),
    )
    # DX GradientType: 0 = left->right, 1 = top->bottom.
    # CSS: 0deg=to top, 90=to right, 180=to bottom, 270=to left; the first
    # color sits at the origin end of the gradient line.
    if angle in (90, 270):  # horizontal
        gtype = 0
        start, end = (first, last) if angle == 90 else (last, first)
    else:  # vertical
        gtype = 1
        start, end = (first, last) if angle == 180 else (last, first)
    filt = (
        "progid:DXImageTransform.Microsoft.Gradient(GradientType=%d,"
        " StartColorStr='%s', EndColorStr='%s')" % (gtype, _to_argb(start), _to_argb(end))
    )
    return filt, first


def _shadow_filter(value):
    """IE6 shadow filter from the first box-shadow/text-shadow.

    Blurred shadows (the third length > 0) use the soft 'Shadow' filter;
    sharp ones use the hard-edged 'DropShadow'.  A zero-offset DropShadow
    is invisible behind an opaque element, so glows must use 'Shadow'.
    """
    if "inset" in value.lower():
        return None
    # strip color tokens first so hex digits aren't read as lengths;
    # offsets are often unitless ("0 0 10px #000")
    lengths = re.findall(r"-?[\d.]+(?:px|em)?", re.sub(_COLOR_RE, " ", value))
    color_m = re.search(_COLOR_RE, value)
    if len(lengths) < 2 or not color_m:
        return None
    hexc = _to_argb(color_m.group(1))
    if not hexc:
        return None
    blur = _px(lengths[2]) if len(lengths) > 2 else 0.0
    name = "Shadow" if blur > 0 else "DropShadow"
    return (
        "progid:DXImageTransform.Microsoft.%s("
        "OffX=%g,OffY=%g,Color='%s')" % (name, _px(lengths[0]), _px(lengths[1]), hexc)
    )


def _transform_decls(body):
    """Down-grade one declaration block to IE6.

    Modern effects are approximated with DXImageTransform filters and CSS
    expressions (both IE6/7-only features); the rest is dropped.
    """
    filters = []
    decls = []
    fixed = False
    jc = re.search(r"justify-content\s*:\s*([^;]+)", body, re.I)
    center = bool(jc and "center" in jc.group(1))
    for d in body.split(";"):
        if ":" not in d:
            continue
        prop, _, val = d.partition(":")
        p = prop.strip().lower()
        v = val.strip()
        if p == "opacity":
            try:
                filters.append("alpha(opacity=%d)" % round(float(v) * 100))
            except ValueError:
                pass
            continue
        if p in ("box-shadow", "text-shadow"):
            f = _shadow_filter(v)
            if f:
                filters.append(f)
            continue
        g = _gradient_filter(v)
        if g:
            filters.append(g[0])
            um = re.search(r"url\([^)]*\)", v)
            if um:
                # Multi-layer background (e.g. gradient over an image):
                # keep the image layer; the gradient paints over it as a
                # filter.  Without this the url() would be dropped and the
                # element would show only the gradient.
                decls.append("background-image: %s" % um.group(0))
            elif p == "background":  # keep a solid fallback color (hex only)
                hexc = _to_hex(g[1])
                if hexc:
                    decls.append("background-color: %s" % hexc)
            continue
        if p in ("filter", "-ms-filter"):
            if "progid:" in v.lower():  # already an IE filter -- keep it
                decls.append("%s: %s" % (prop.strip(), v))
            continue
        if BAD_PROP_RE.match(p):
            continue
        if p == "display" and re.search(r"(inline-)?(flex|grid)", v):
            v = "inline" if "inline" in v else "block"
            if center and v == "block":
                decls.append("text-align: center")
        elif p == "position" and v.lower() in ("fixed", "sticky"):
            v = "absolute"
            fixed = True
        decls.append("%s: %s" % (prop.strip(), v))
    # No extra work for fixed/sticky here: the element is absolute with its
    # static top/left kept, and the HTML step (_pin_fixed_elements) adds a
    # JS scroll handler.  CSS expression() -- the classic IE6 "position:
    # fixed" hack -- is ignored in the standards mode the proxy forces via
    # the XHTML Strict doctype, so it can't do the pinning.
    if filters:
        # IE6 only honours the FIRST filter in a list -- keep the most
        # visually significant one (gradient > shadow > opacity).
        def rank(f):
            fl = f.lower()
            if "gradient" in fl:
                return 0
            if "dropshadow" in fl:
                return 1
            return 2
        decls.append("filter: %s" % min(filters, key=rank))
    return "; ".join(decls)


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


def _rewrite_css_urls(css, base, abs_base=None):
    def sub(m):
        inner = m.group(2).strip().strip("'\''")
        if not inner or inner.lower().startswith(("data:", "#")):
            return m.group(0)
        absu = urljoin(base, inner)
        return 'url("%s")' % to_proxy(absu, abs_base)

    return re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", sub, css)


def _filter_rules(css):
    def sub(m):
        sel, body = m.group(1), m.group(2)
        sel = sel.replace("::", ":")  # IE6 uses single-colon pseudos
        if BAD_SELECTOR_RE.search(sel):
            return ""
        decls = _transform_decls(body)
        if not decls:
            return ""
        # IE6 ignores filters on body/html -- and worse, a filter on an
        # ancestor breaks the filters of its descendants, so a body filter
        # (e.g. from text-shadow) kills every gradient on the page.
        if re.search(r"(^|,\s*)\s*(body|html)\s*($|,)", sel, re.I):
            decls = "; ".join(
                d for d in decls.split(";") if not d.strip().lower().startswith("filter")
            )
            if not decls.strip():
                return ""
        return "%s { %s }" % (sel, decls)

    return re.sub(r"([^{}]+)\{([^{}]*)\}", sub, css)


def _body_gradient_filter(css):
    """If the body rule has a gradient background, return the DX gradient
    filter for it.  IE6 can't put filters on body, so the HTML step paints
    it on a full-viewport background div instead (matching the original
    background-attachment:fixed look)."""
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = rm.group(1).strip()
        if not re.fullmatch(r"(?:body|html)(?:\s*,\s*(?:body|html))*", sel, re.I):
            continue
        bm = re.search(
            r"(?<![\w-])background(?:-image)?\s*:\s*([^;]+)", rm.group(2), re.I
        )
        if bm and "gradient" in bm.group(1).lower():
            g = _gradient_filter(bm.group(1))
            if g:
                return g[0]
    return None


def transform_css(css, base, abs_base=None):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = _strip_at_blocks(
        css, ["font-face", "keyframes", "-webkit-keyframes", "-moz-keyframes"]
    )
    css = _strip_at_blocks(css, ["media", "supports"])
    # rgba -> rgb, keeping the alpha as a 4th value: IE6 ignores such
    # declarations in CSS properties (as before), but _parse_color can
    # recover the alpha for the DX filters.
    css = re.sub(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+%?)\s*\)",
        r"rgb(\1,\2,\3,\4)", css, re.I,
    )
    css = re.sub(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", r"rgb(\1,\2,\3)", css, re.I
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
    css = _rewrite_css_urls(css, base, abs_base)
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

# Tiny ES5 shims so common modern inline scripts at least parse and degrade
# gracefully in IE6 (which lacks console, querySelector, Audio, addEventListener,
# Array.prototype.forEach, ...). Injected right after <head>.
IE6_SHIM = (
    "<script>"
    "if(!window.console){window.console={log:function(){},warn:function(){},"
    "error:function(){}};}"
    "if(!Array.prototype.forEach){Array.prototype.forEach=function(f){for("
    "var i=0;i<this.length;i++){f(this[i],i,this);}};}"
    "if(!document.querySelector){document.querySelector=function(){return null;};}"
    "if(!document.querySelectorAll){document.querySelectorAll=function(){return[];};}"
    "if(!window.Audio){window.Audio=function(){return{play:function(){},pause:"
    "function(){},currentTime:0};};}"
    "if(!document.addEventListener){document.addEventListener=function(t,f){var "
    "d=document;if(t==='DOMContentLoaded'){if(d.readyState!=='loading'){f();}"
    "else{d.attachEvent('onreadystatechange',function(){if(d.readyState!="
    "'loading'){f();}});}}else{d.attachEvent('on'+t,f);}};}"
    "if(!window.addEventListener){window.addEventListener=function(t,f){window."
    "attachEvent('on'+t,f);};}"
    "</script>"
)


def _downgrade_js(src):
    """Light ES6->ES5 pass for inline scripts: const/let -> var and block-body
    arrow functions -> function expressions. Deeper syntax (template literals,
    class, async) is left alone; if it remains the script simply fails in IE6."""
    s = re.sub(r"\b(const|let)\s+", "var ", src)
    # block-body arrows: "(a, b) => {" -> "function(a, b){"
    s = re.sub(r"(\([^()]*\))\s*=>\s*\{", r"function\1{", s)
    # bare-identifier arrows: "x => {" -> "function(x){"
    s = re.sub(r"([A-Za-z_$][\w$]*)\s*=>\s*\{", r"function(\1){", s)
    return s

SKIP_URL_PREFIXES = ("data:", "#", "javascript:", "mailto:", "tel:", "about:")


def _rewrite_attrs(tag, base, abs_base=None):
    def sub(a):
        name = a.group(1).lower()
        val = next(g for g in a.groups()[1:] if g is not None)
        if name == "srcset":
            return ""
        if name == "style":
            return 'style="%s"' % _rewrite_css_urls(_transform_decls(val), base).replace(
                '"', "'"
            )
        if name in ("src", "href", "poster") and val:
            v = val.strip()
            if v.lower().startswith(SKIP_URL_PREFIXES):
                return a.group(0)
            absu = urljoin(base, v)
            if urlparse(absu).scheme in ("http", "https"):
                v = to_proxy(absu, abs_base)
            return '%s="%s"' % (a.group(1), v)
        return a.group(0)

    return ATTR_RE.sub(sub, tag)


def transform_html(html, base, abs_base=None):
    # <base href> changes how relative URLs resolve -- honour it first.
    bm = re.search(r"<base[^>]+href\s*=\s*['\"]([^'\"]+)['\"]", html, re.I)
    if bm:
        base = urljoin(base, bm.group(1))

    # Drop ES modules and ad scripts; strip defer/async from the rest;
    # down-grade inline script syntax to ES5.
    html = re.sub(
        r"<script[^>]*type\s*=\s*['\"]?module['\"]?[^>]*>.*?</script\s*>",
        "",
        html,
        flags=re.I | re.S,
    )

    def _inline_script(m):
        open_tag, body = m.group(1), m.group(2)
        if re.search(r"\bsrc\s*=", open_tag, re.I):
            return m.group(0)
        return open_tag + _downgrade_js(body) + "</script>"

    html = re.sub(
        r"(<script\b[^>]*>)(.*?)</script\s*>",
        _inline_script,
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

    # Media elements IE6 cannot show: sized placeholders where possible.
    def _media_ph(m):
        tag, attrs = m.group(1), (m.group(2) or "")
        if tag.lower() == "audio":
            return "<span></span>"
        w = re.search(r'width\s*=\s*["\']?(\d+)', attrs, re.I)
        h = re.search(r'height\s*=\s*["\']?(\d+)', attrs, re.I)
        if w and h:
            return (
                "<span style='display:block;width:%spx;height:%spx;"
                "background-color:#dddddd;border:1px solid #999999'></span>"
            ) % (w.group(1), h.group(1))
        return "<span></span>"

    html = re.sub(
        r"<(video|audio|canvas)(\s[^>]*)?>.*?</\1\s*>",
        _media_ph,
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

    # ES5 compatibility shims, before any page script can run.
    if re.search(r"<head[^>]*>", html, re.I):
        html = re.sub(
            r"(<head[^>]*>)", lambda m: m.group(1) + IE6_SHIM,
            html,
            count=1,
            flags=re.I,
        )

    # Rewrite every tag's URL-bearing attributes through the proxy.
    html = re.sub(
        r"<[a-zA-Z][^>]*>",
        lambda m: _rewrite_attrs(m.group(0), base, abs_base),
        html,
    )
    return html


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
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
        return e.code, dict(e.headers.items() or []), e.read()
    except Exception:
        return None, None, None
    return resp.status, dict(resp.headers.items()), resp.read()


def _flex_container_classes(css):
    """Class names of row-direction flex containers (the ones that lay their
    children out side by side). Column flex degrades fine to plain block."""
    classes = set()
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        if not re.search(r"display\s*:\s*(?:inline-)?flex\b", body, re.I):
            continue
        if re.search(r"flex-direction\s*:\s*column", body, re.I):
            continue
        for cm in re.finditer(r"\.([A-Za-z_][\w-]*)", sel):
            classes.add(cm.group(1))
    return classes


_FLEX_ROW_CHILD_TAGS = set(
    "div section article aside nav main header footer ul ol table form fieldset".split()
)
_VOID_TAGS = set(
    "area base br col embed hr img input link meta param source track wbr".split()
)


def _fixed_px_widths(css):
    """class name -> approximate outer pixel width (content width + horizontal
    padding, 40..600px) from simple class rules."""
    def hpad(body):
        m = re.search(r"(?<![-\w])padding\s*:\s*([^;]+)", body, re.I)
        if m:
            vals = [int(x) for x in re.findall(r"(\d+)px", m.group(1))]
            if len(vals) == 1:
                return vals[0] * 2
            if len(vals) == 2:
                return vals[1] * 2
            if len(vals) == 3:
                return vals[1] * 2
            if len(vals) == 4:
                return vals[1] + vals[3]
        tot = 0
        for side in ("left", "right"):
            m = re.search(r"padding-%s\s*:\s*(\d+)px" % side, body, re.I)
            if m:
                tot += int(m.group(1))
        return tot

    widths = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        wm = re.search(r"(?<![-\w])width\s*:\s*(\d+)px", body, re.I)
        if not wm:
            continue
        w = int(wm.group(1)) + hpad(body)
        if not 40 <= w <= 700:
            continue
        for cm in re.finditer(r"\.([A-Za-z_][\w-]*)", sel):
            widths.setdefault(cm.group(1), w)
    return widths


def _pinned_classes(css):
    """Class names whose rules use position: sticky/fixed (become absolute,
    pinned by a JS scroll handler, i.e. they stay put while the page
    scrolls)."""
    pinned = set()
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r"(?<![-\w])position\s*:\s*(?:sticky|fixed)",
                         rm.group(2), re.I):
            continue
        for cm in re.finditer(r"\.([A-Za-z_][\w-]*)", rm.group(1)):
            pinned.add(cm.group(1))
    return pinned


def _flex_row_layout(html, flex_classes, widths, pinned=None):
    """IE6 has no flexbox. For each row-flex container element found in the
    HTML: children with a known fixed pixel width (e.g. a fixed-width sidebar)
    float:left (height:auto so the float box wraps all their content); their
    siblings get overflow:hidden so they wrap around the float and take the
    remaining width -- the classic pre-flex two-column pattern, rock solid in
    IE6. If such a child is also position:sticky/fixed (pinned), it becomes
    position:absolute instead (floats can't stay put while scrolling; the
    JS pin script keeps them in place) and its siblings get
    margin-left equal to its outer width. If no child has a known width, fall
    back to making all of them inline-block."""
    if not flex_classes:
        return html
    pinned = pinned or set()
    edits = []  # (open_start, open_end, css-to-append)
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        attrs = m.group(2) or ""
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm:
            continue
        if not (set(cm.group(1).split()) & flex_classes):
            continue
        children = []  # (start, end, classes-set, has-inline-display)
        depth = 1
        start = m.end()
        for tm in re.finditer(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)?>", html[start:]):
            tag, tattrs = tm.group(1).lower(), tm.group(2) or ""
            if tm.group(0)[1] == "/":  # closing tag
                depth -= 1
                if depth == 0:
                    break
                continue
            if tag in _VOID_TAGS or tattrs.rstrip().endswith("/"):
                continue
            if depth == 1 and tag in _FLEX_ROW_CHILD_TAGS:
                ccm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', tattrs, re.I)
                sm = re.search(r'style\s*=\s*["\']([^"\']*)["\']', tattrs, re.I)
                children.append((
                    start + tm.start(),
                    start + tm.end(),
                    set(ccm.group(1).split()) if ccm else set(),
                    bool(sm and re.search(r"display", sm.group(1), re.I)),
                ))
            depth += 1
        if not children:
            continue
        has_floated = any(
            (not cd) and any(k in widths for k in cclasses)
            for _, _, cclasses, cd in children
        )
        pinned_w = next(
            (widths[k] for _, _, ccs, cd in children
             if not cd for k in ccs if k in pinned and k in widths),
            None,
        )
        for cs, ce, cclasses, cd in children:
            if cd:  # keep explicit inline display untouched
                continue
            if pinned_w is not None and any(k in pinned for k in cclasses):
                add = "position:absolute;left:0;"
            elif has_floated and any(k in widths for k in cclasses):
                add = "float:left;height:auto;"
            elif pinned_w is not None:
                add = "margin-left:%dpx;" % pinned_w
            elif has_floated:
                add = "overflow:hidden;"
            else:
                add = "display:inline-block;vertical-align:top;"
            edits.append((cs, ce, add))
    for open_start, open_end, add in sorted(edits, reverse=True):
        tag_text = html[open_start:open_end]
        if re.search(r"\bstyle\s*=\s*[\"']", tag_text, re.I):
            tag_text = re.sub(
                r"(\bstyle\s*=\s*[\"'])([^\"']*)",
                lambda mm: mm.group(1) + mm.group(2) + add,
                tag_text,
                count=1,
                flags=re.I,
            )
        else:
            tag_text = tag_text[:-1] + ' style="%s">' % add
        html = html[:open_start] + tag_text + html[open_end:]
    return html


# IE6 ignores CSS expression() in standards mode (which the proxy forces
# via the XHTML Strict doctype), so position:fixed elements are pinned by a
# tiny scroll handler instead.  Elements are found by class (from the CSS)
# or by an inline position:fixed style, and get a unique id.
PIN_SCRIPT = (
    "<script>(function(){var ids=[%s];var els=[];var i;"
    "for(i=0;i<ids.length;i++){var el=document.getElementById(ids[i]);"
    "if(el)els.push(el);}"
    "if(!els.length)return;"
    "var tops=[],lefts=[];"
    "function off(el,p){var v=el.currentStyle?el.currentStyle[p]:'';"
    "var n=parseInt(v,10);return isNaN(n)?0:n;}"
    "function place(){var dl=document.documentElement,b=document.body;"
    "var sl=(dl&&dl.scrollLeft)||b.scrollLeft||0;"
    "var st=(dl&&dl.scrollTop)||b.scrollTop||0;"
    "for(var j=0;j<els.length;j++){"
    "els[j].style.left=(sl+lefts[j])+\"px\";"
    "els[j].style.top=(st+tops[j])+\"px\";}}"
    "function init(){for(i=0;i<els.length;i++){"
    "tops.push(off(els[i],'top'));lefts.push(off(els[i],'left'));}"
    "place();"
    "if(window.attachEvent)window.attachEvent('onscroll',place);"
    "else if(window.addEventListener)window.addEventListener('scroll',place,false);}"
    "if(window.attachEvent)window.attachEvent('onload',init);"
    "else if(window.addEventListener)window.addEventListener('load',init,false);"
    "else init();"
    "})()</script>"
)


def _pin_fixed_elements(html, pinned):
    """Give position:fixed/sticky elements unique ids and inject the
    PIN_SCRIPT scroll handler before </body> so they stay put while the
    page scrolls (CSS expression() is ignored in IE6 standards mode)."""
    if not pinned:
        return html
    ids = []
    edits = []
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        attrs = m.group(2) or ""
        if re.search(r"\bid\s*=", attrs, re.I):
            continue  # already has an id; leave it alone
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        by_class = bool(cm and set(cm.group(1).split()) & pinned)
        sm = re.search(r'style\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        by_style = bool(
            sm and re.search(r"position\s*:\s*(fixed|sticky)", sm.group(1), re.I)
        )
        if not (by_class or by_style):
            continue
        pid = "ie6pin-%d" % len(ids)
        ids.append(pid)
        edits.append((m.start(), m.end(), pid,
                      "ie6bg" in (cm.group(1) if cm else "")))
    for start, end, pid, is_bg in sorted(edits, reverse=True):
        tag = html[start:end]
        new = tag[:-1] + ' id="%s"' % pid
        if not is_bg:
            # IE6 paints filtered elements (like the body-gradient div)
            # above z-index:auto positioned siblings; an explicit z-index
            # keeps pinned content on top of it.
            if re.search(r"\bstyle\s*=\s*[\"']", new, re.I):
                new = re.sub(
                    r"(\bstyle\s*=\s*[\"'])([^\"']*)",
                    lambda mm: mm.group(1) + mm.group(2) + "z-index:1;",
                    new, count=1, flags=re.I,
                )
            else:
                new += ' style="z-index:1"'
        html = html[:start] + new + ">" + html[end:]
    if not ids:
        return html
    script = PIN_SCRIPT % ",".join('"%s"' % i for i in ids)
    if re.search(r"</body\s*>", html, re.I):
        html = re.sub(r"</body\s*>", script + r"</body>", html, count=1, flags=re.I)
    else:
        html += script
    return html


# Viewport units (vw/vh) are statically approximated as 1024x768 in the CSS
# (a safe fallback), but elements sized in viewport units should track the
# real window: they get a unique id and a resize handler, like the pinned
# elements.  box-sizing:border-box is not understood by IE6, so the element's
# own padding is subtracted to approximate it.
VP_SCRIPT = (
    "<script>(function(){var els=[%(els)s];"
    "function px(v){var n=parseInt(v,10);return isNaN(n)?0:n;}"
    "function size(){"
    "var dl=document.documentElement,b=document.body;"
    "var h=(dl&&dl.clientHeight)||b.clientHeight||768;"
    "var w=(dl&&dl.clientWidth)||b.clientWidth||1024;"
    "for(var i=0;i<els.length;i++){"
    "var e=document.getElementById(els[i].id);if(!e)continue;"
    "var cs=e.currentStyle;"
    "var pad=els[i].p=='height'"
    "?px(cs.paddingTop)+px(cs.paddingBottom)"
    ":px(cs.paddingLeft)+px(cs.paddingRight);"
    "var v=els[i].f*(els[i].p=='height'?h:w)-pad;"
    "if(v>0)e.style[els[i].p]=v+'px';}}"
    "function init(){size();"
    "if(window.attachEvent)window.attachEvent('onresize',size);"
    "else if(window.addEventListener)window.addEventListener('resize',size,false);}"
    "if(window.attachEvent)window.attachEvent('onload',init);"
    "else if(window.addEventListener)window.addEventListener('load',init,false);"
    "else init();"
    "})()</script>"
)


def _viewport_classes(css):
    """Class names whose rules use vw/vh lengths, mapped to the (property,
    fraction-of-viewport) pairs, e.g. {'sidenav': [('height', 1.0)]}."""
    vp = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        for prop, unit in (("height", "vh"), ("width", "vw")):
            pm = re.search(
                r"(?<![\w-])%s\s*:\s*([\d.]+)\s*%s" % (prop, unit),
                rm.group(2), re.I,
            )
            if not pm:
                continue
            for cm in re.finditer(r"\.([A-Za-z_][\w-]*)", rm.group(1)):
                vp.setdefault(cm.group(1), []).append(
                    (prop, float(pm.group(1)) / 100.0)
                )
    return vp


def _viewport_elements(html, vp):
    """Give vw/vh-sized elements unique ids and inject the VP_SCRIPT resize
    handler before </body> so they track the real window size (the CSS only
    carries a static 1024x768 approximation)."""
    if not vp:
        return html
    specs = []  # (id, prop, factor)
    edits = []  # (start, end, id) -- only elements needing a new id
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        attrs = m.group(2) or ""
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm:
            continue
        pairs = []
        for cls in set(cm.group(1).split()) & set(vp):
            pairs.extend(vp[cls])
        if not pairs:
            continue
        im = re.search(r'\bid\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if im:
            vid = im.group(1)  # reuse an existing id (e.g. from pinning)
        else:
            vid = "ie6vp-%d" % len(edits)
            edits.append((m.start(), m.end(), vid))
        for prop, factor in pairs:  # the script sizes all of its props
            specs.append((vid, prop, factor))
    for start, end, vid in sorted(edits, reverse=True):
        tag = html[start:end]
        html = html[:start] + tag[:-1] + ' id="%s">' % vid + html[end:]
    if not specs:
        return html
    els = ",".join(
        '{id:"%s",p:"%s",f:%g}' % (vid, prop, factor) for vid, prop, factor in specs
    )
    script = VP_SCRIPT % {"els": els}
    if re.search(r"</body\s*>", html, re.I):
        html = re.sub(r"</body\s*>", script + r"</body>", html, count=1, flags=re.I)
    else:
        html += script
    return html


def _bg_image_classes(css):
    """Class -> (url, 'cover'|'contain') for rules with a background image
    AND background-size.  IE6 can't scale background images (and a
    width:100%-ish div with no height collapses to nothing), so the HTML
    step replaces the image with a nested <img> that scales like cover."""
    out = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        body = rm.group(2)
        sm = re.search(r"background-size\s*:\s*(cover|contain)", body, re.I)
        if not sm:
            continue
        bm = re.search(
            r"(?<![\w-])background(?:-image)?\s*:\s*[^;]*?url\(\s*(['\"]?)(.*?)\1\s*\)",
            body, re.I,
        )
        if not bm:
            continue
        # only the LAST class of the selector (most specific) -- mapping
        # ancestor classes too would inject the img into their elements
        cls = re.findall(r"\.([A-Za-z_][\w-]*)", rm.group(1))
        if cls:
            out[cls[-1]] = (bm.group(2), sm.group(1).lower())
    return out


def _bg_image_elements(html, bg, base, abs_base=None):
    """Inject a scaling <img> as the first child of elements whose class has
    a background image with background-size (see _bg_image_classes), and
    suppress the element's own background image (the nested img replaces
    it; without this both would paint, misaligned)."""
    if not bg:
        return html
    edits = []
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        tag = m.group(1).lower()
        if tag in _VOID_TAGS or tag == "img":
            continue
        attrs = m.group(2) or ""
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm:
            continue
        hit = None
        for cls in set(cm.group(1).split()):
            if cls in bg:
                hit = bg[cls]
                break
        if not hit:
            continue
        raw_url, _size = hit
        img_url = to_proxy(urljoin(base, html_unescape(raw_url)), abs_base)
        tag_text = html[m.start():m.end()]
        if re.search(r"\bstyle\s*=\s*[\"']", tag_text, re.I):
            tag_text = re.sub(
                r"(\bstyle\s*=\s*[\"'])([^\"']*)",
                lambda mm: mm.group(1) + mm.group(2) + "background-image:none;",
                tag_text, count=1, flags=re.I,
            )
        else:
            tag_text = tag_text[:-1] + ' style="background-image:none;">'
        img = (
            '<img src="%s" alt="" '
            'style="display:block;width:100%%;border:0">' % img_url
        )
        edits.append((m.start(), m.end(), tag_text + img))
    for start, end, repl in sorted(edits, reverse=True):
        html = html[:start] + repl + html[end:]
    return html


# margin-top:auto (the flexbox idiom for "push to the bottom of the
# column") is meaningless in IE6.  When the element sits inside a pinned
# (positioned, full-height) container we anchor it to that container's
# bottom with position:absolute instead.
_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")


def _margin_auto_classes(css):
    """Classes whose rules use margin-top:auto."""
    out = set()
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"(?<![\w-])margin-top\s*:\s*auto", rm.group(2), re.I):
            out.update(re.findall(r"\.([A-Za-z_][\w-]*)", rm.group(1)))
    return out


def _container_metrics(css, classes):
    """Class -> {"pl", "pb", "w"} (padding-left, padding-bottom, width in
    px) from the element's own rule (selector ends with the class), so
    absolutely-positioned children can be laid out like in-flow content."""
    out = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel_text = rm.group(1).strip()
        body = rm.group(2)
        for c in classes:
            if not re.search(r"(?<![\w-])" + re.escape(c) + r"\s*$",
                             sel_text):
                continue
            m = {"pl": 0, "pb": 0, "w": None}
            pm = re.search(r"(?<![\w-])padding\s*:\s*([^;]+);?",
                           body, re.I)
            if pm:
                parts = re.findall(r"\d+px", pm.group(1))
                if parts:
                    vals = [int(x[:-2]) for x in parts]
                    if len(vals) == 1:
                        t = r = b = l = vals[0]
                    elif len(vals) == 2:
                        t = b = vals[0]; r = l = vals[1]
                    elif len(vals) == 3:
                        t, r, b = vals; l = r
                    else:
                        t, r, b, l = vals[:4]
                    m["pl"], m["pb"] = l, b
            for prop, key in (("padding-left", "pl"), ("padding-bottom", "pb")):
                em = re.search(r"(?<![\w-])" + prop + r"\s*:\s*(\d+)px",
                               body, re.I)
                if em:
                    m[key] = int(em.group(1))
            wm = re.search(r"(?<![\w-])width\s*:\s*(\d+)px", body, re.I)
            if wm:
                m["w"] = int(wm.group(1))
            out[c] = m
    return out


def _nearest_ancestor(html, pos):
    """Match of the nearest open ancestor tag before `pos` (or None)."""
    stack = []
    for tm in _TAG_RE.finditer(html, 0, pos):
        name, attrs = tm.group(1).lower(), tm.group(2) or ""
        if tm.group(0)[1] == "/":
            if stack and stack[-1].group(1).lower() == name:
                stack.pop()
        elif name not in _VOID_TAGS and not attrs.rstrip().endswith("/"):
            stack.append(tm)
    return stack[-1] if stack else None


def _bottom_pin_elements(html, bottom_classes, pinned, pads):
    """Anchor margin-top:auto elements to the bottom of their pinned
    (positioned) ancestor -- the IE6 equivalent of the flexbox idiom."""
    if not bottom_classes:
        return html
    edits = []
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        if m.group(1).lower() in _VOID_TAGS:
            continue
        attrs = m.group(2) or ""
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm or not (set(cm.group(1).split()) & bottom_classes):
            continue
        anc = _nearest_ancestor(html, m.start())
        if not anc:
            continue
        aattrs = anc.group(2) or ""
        acm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', aattrs, re.I)
        asm = re.search(r'style\s*=\s*["\']([^"\']*)["\']', aattrs, re.I)
        is_pinned = bool(
            (acm and set(acm.group(1).split()) & pinned)
            or (asm and re.search(
                r"position\s*:\s*(absolute|fixed)", asm.group(1), re.I))
        )
        if not is_pinned:
            continue
        # Lay the block out like in-flow content: inset by the container's
        # padding, same width as the container's content box (IE6 doesn't
        # reliably stretch absolutely-positioned elements with left+right).
        pl = pb = 0
        w = None
        if acm:
            for c in set(acm.group(1).split()):
                mm = pads.get(c)
                if mm:
                    pl = max(pl, mm["pl"])
                    pb = max(pb, mm["pb"])
                    w = mm["w"] if w is None else w
        if w is not None:
            add = ("position:absolute;left:%dpx;width:%dpx;bottom:%dpx;"
                   % (pl, w, pb))
        else:
            add = "position:absolute;left:0;right:0;bottom:%dpx;" % pb
        tag_text = html[m.start():m.end()]
        if re.search(r"\bstyle\s*=\s*[\"']", tag_text, re.I):
            tag_text = re.sub(
                r"(\bstyle\s*=\s*[\"'])([^\"']*)",
                lambda mm: mm.group(1) + mm.group(2) + add,
                tag_text, count=1, flags=re.I,
            )
        else:
            tag_text = tag_text[:-1] + ' style="%s">' % add
        edits.append((m.start(), m.end(), tag_text))
    for start, end, repl in sorted(edits, reverse=True):
        html = html[:start] + repl + html[end:]
    return html


def _make_tls_context():
    """Self-signed cert via the openssl CLI, cached in a temp dir."""
    d = os.path.join(tempfile.gettempdir(), "ie6proxy-tls")
    os.makedirs(d, exist_ok=True)
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        try:
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", key, "-out", cert, "-days", "3650",
                    "-nodes", "-subj", "/CN=IE6Proxy",
                ],
                check=True, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        return ctx
    except (ssl.SSLError, OSError):
        return None


def _is_ad_url(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in AD_DOMAINS)


LANDING = """<!DOCTYPE html>
<html><head><title>IE6 Proxy</title></head>
<body bgcolor="#ffffff" text="#000000">
<center>
<h1>Internet Explorer 6 Proxy</h1>
<p>This proxy only works when configured as your <b>system / LAN proxy</b>.</p>
<p>Set your browser's proxy to <tt>__ADDR__</tt> (Internet Options &rarr;
Connections &rarr; LAN settings &rarr; Proxy) and then browse normally.</p>
<p style="font-size:11px;color:#666666">
All pages are rewritten for IE6 (HTML4, no CSS3, no flexbox), HTTPS is
downgraded to HTTP, and ads are blocked.
</p>
</center>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "IE6Proxy/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _is_foreign_host(self):
        """True when the Host header points at a site other than this proxy.

        We are bound to 0.0.0.0, so direct browsing may arrive with any of
        our IPs in the Host header; treat anything on our port (or loopback)
        as local, and everything else as proxied traffic.
        """
        host = (self.headers.get("Host") or "").lower()
        if not host:
            return False
        hostname, _, port = host.partition(":")
        if hostname in ("localhost", "127.0.0.1"):
            return False
        try:
            if int(port) == self.server.server_address[1]:
                return False
        except ValueError:
            pass
        return True

    def _abs_base(self):
        """Flag/URL for system-proxy mode (absolute-form, foreign-Host,
        CONNECT-tunnel requests). Non-None tells the rewriters to keep
        original absolute URLs instead of wrapping them in the proxy path
        scheme -- the browser routes all http traffic through us anyway."""
        return "http://%s:%d" % (
            self.client_address[0], self.server.server_address[1]
        )

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

    def do_CONNECT(self):
        # System-proxy mode: the browser tunnels an HTTPS site through us.
        # Answer 200, terminate TLS with our self-signed cert, then let the
        # handle() loop read the real (origin-form) request over the tunnel;
        # it is downgraded to plain HTTP upstream.
        self.send_response_only(200)
        self.end_headers()
        ctx = getattr(self.server, "ssl_ctx", None)
        if ctx is None:
            self.wfile.write(
                b"CONNECT failed: openssl not found (no TLS support)\r\n"
            )
            self.close_connection = True
            return
        try:
            tls = ctx.wrap_socket(self.connection, server_side=True)
        except (ssl.SSLError, OSError):
            self.close_connection = True
            return
        self.connection = tls
        self.rfile = tls.makefile("rb", self.rbufsize)
        self.wfile = tls.makefile("wb")
        self.close_connection = False

    # -- main ---------------------------------------------------------------
    def _handle(self, head_only):
        abs_base = None
        # Absolute-form request target ("GET http://google.com/ HTTP/1.1").
        if self.path.lower().startswith(("http://", "https://")):
            url = self.path
            # Tolerate a doubled proxy path ("http://host/http/host2/..."):
            # the browser resolved one of our rewritten links against the
            # origin site instead of the proxy.
            m = re.match(r"^https?://[^/]+(/.*)$", self.path, re.I)
            if m and from_proxy(m.group(1)):
                url = from_proxy(m.group(1))
            abs_base = self._abs_base()
        elif self._is_foreign_host():
            # Origin-form request from a system proxy (after CONNECT or as a
            # plain proxied request): the target is in the Host header.
            url = "http://%s%s" % (
                self.headers.get("Host"), self.path
            )
            abs_base = self._abs_base()
        else:
            # Direct URLs (http://proxy:port/http://host/...) are not a thing;
            # this proxy is only meant to be used as a system/LAN proxy.
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(
                    LANDING.replace(
                        "__ADDR__",
                        "%s:%d" % (
                            self.client_address[0],
                            self.server.server_address[1],
                        ),
                    ),
                    "text/html",
                    head_only=head_only,
                )
                return
            self._send(
                "<html><body>Not found.<br>This proxy is only used via "
                "system/LAN proxy settings, not by typing proxy URLs.</body>"
                "</html>",
                "text/html",
                404,
                head_only,
            )
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

        # Downgrade HTTPS to HTTP for the upstream fetch (IE6-era trust).
        url = re.sub(r"^https://", "http://", url)

        # --- fetch upstream ---
        status, headers, body = http_get(url, timeout=20)
        if status is None:
            self._send(
                b"Upstream error: could not reach " + url.encode("utf-8", "replace"),
                "text/plain", 502, head_only,
            )
            return
        if 300 <= status < 400 and headers.get("Location"):
            loc = urljoin(url, headers["Location"])
            self._redirect(to_proxy(loc, abs_base))
            return
        ctype = headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        charset_m = re.search(r"charset=([\w-]+)", headers.get("Content-Type") or "", re.I)
        charset = charset_m.group(1).lower() if charset_m else "utf-8"

        # --- transform by type ---
        if ctype == "text/html":
            text = body.decode(charset, errors="replace")
            # Find row-flex containers (from <style> blocks and linked
            # stylesheets) so their children can be laid out side by side.
            flex_classes = set()
            widths = {}
            pinned = set()
            vp = {}
            bg = {}
            bottom = set()
            body_filter = None
            css_chunks = []
            for sm in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>",
                                  text, re.I | re.S):
                css_chunks.append(sm.group(1))
                flex_classes |= _flex_container_classes(sm.group(1))
                widths.update(_fixed_px_widths(sm.group(1)))
                pinned |= _pinned_classes(sm.group(1))
                for k, v in _viewport_classes(sm.group(1)).items():
                    vp.setdefault(k, []).extend(v)
                bg.update(_bg_image_classes(sm.group(1)))
                bottom |= _margin_auto_classes(sm.group(1))
                body_filter = body_filter or _body_gradient_filter(sm.group(1))
            for lm in re.finditer(r"<link\b[^>]*>", text, re.I):
                tag = lm.group(0)
                if not re.search(r'rel\s*=\s*["\']?stylesheet', tag, re.I):
                    continue
                hm = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.I)
                if not hm:
                    continue
                su = re.sub(r"^https://", "http://",
                            urljoin(url, html_unescape(hm.group(1))))
                if _is_ad_url(su):
                    continue
                _, _, sdata = http_get(su, timeout=5)
                if sdata:
                    scss = sdata.decode("utf-8", "replace")
                    css_chunks.append(scss)
                    flex_classes |= _flex_container_classes(scss)
                    widths.update(_fixed_px_widths(scss))
                    pinned |= _pinned_classes(scss)
                    for k, v in _viewport_classes(scss).items():
                        vp.setdefault(k, []).extend(v)
                    bg.update(_bg_image_classes(scss))
                    bottom |= _margin_auto_classes(scss)
                    body_filter = body_filter or _body_gradient_filter(scss)
            text = transform_html(text, url, abs_base)
            text = _bg_image_elements(text, bg, url, abs_base)
            if body_filter:
                # IE6 can't filter body: paint the gradient on a pinned,
                # full-viewport div behind everything (z-index:-1 renders
                # above the body background color in IE6).
                pinned.add("ie6bg")
                vp.setdefault("ie6bg", []).extend(
                    [("width", 1.0), ("height", 1.0)]
                )
                bgdiv = (
                    '<div class="ie6bg" style="position:absolute;left:0;top:0;'
                    'width:100%%;height:100%%;z-index:-1;filter:%s"></div>'
                    % body_filter
                )
                text = re.sub(
                    r"(<body\b[^>]*>)", r"\1" + bgdiv, text, count=1, flags=re.I
                )
            text = _flex_row_layout(text, flex_classes, widths, pinned)
            text = _pin_fixed_elements(text, pinned)
            text = _viewport_elements(text, vp)
            text = _bottom_pin_elements(
                text, bottom, pinned,
                _container_metrics("\n".join(css_chunks), pinned))
            self._send(text, "text/html", 200, head_only)
            return
        if ctype == "text/css":
            text = body.decode(charset, errors="replace")
            text = transform_css(text, url, abs_base)
            self._send(text, "text/css", 200, head_only)
            return
        if ctype == "image/webp" and _webp_to_png:
            body = _webp_to_png(body)
            ctype = "image/png"

        self._send(body, ctype, 200, head_only)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.ssl_ctx = _make_tls_context()
    print("IE6 proxy listening on http://%s:%d/" % (HOST, port))
    print("Set it as your system/LAN proxy: <this machine's IP>:%d" % port)
    if srv.ssl_ctx is None:
        print(
            "Note: openssl not found -- CONNECT (HTTPS sites in "
            "system-proxy mode) is disabled."
        )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
