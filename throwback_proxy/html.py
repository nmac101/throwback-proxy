"""HTML rewriting: HTML5 tag down-conversion, ES5 script down-grading,
ad/module script removal, and URL rewriting through the proxy."""

import re
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .config import XHTML_STRICT_DOCTYPE
from .config import FIX_IE_CSS_LIMIT_PATH
from .css import _media_query_true, _rewrite_css_urls, _transform_decls
from .markup import TAG_MAP, protect_raw_text
from .metrics import BOX_METRICS_JS, VIEWPORT_SCROLL_JS
from .urls import to_proxy

ATTR_RE = re.compile(
    r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"""
)

# Tiny ES5 shims so common modern inline scripts at least parse and degrade
# gracefully in IE6 (which lacks console, Audio, addEventListener,
# Array.prototype.forEach, ...). Injected right after <head>.
IE6_SHIM = (
    "<script>"
    # IE6 otherwise re-requests CSS backgrounds during repaint/hover. Run
    # before linked stylesheets; unsupported IE builds must fail harmlessly.
    "try{document.execCommand('BackgroundImageCache',false,true);}catch(ie6CacheError){}"
    + BOX_METRICS_JS + VIEWPORT_SCROLL_JS +
    "if(!window.console){window.console={log:function(){},warn:function(){},"
    "error:function(){},info:function(){},debug:function(){}};}"
    "if(!Array.prototype.indexOf){Array.prototype.indexOf=function(x,i){for(i="
    "i||0;i<this.length;i++){if(this[i]===x){return i;}}return -1;};}"
    "if(!Array.prototype.map){Array.prototype.map=function(f){var r=[];for(var "
    "i=0;i<this.length;i++){r[i]=f(this[i],i,this);}return r;};}"
    "if(!Array.prototype.filter){Array.prototype.filter=function(f){var r=[];"
    "for(var i=0;i<this.length;i++){if(f(this[i],i,this)){r.push(this[i]);}}"
    "return r;};}"
    "if(!String.prototype.trim){String.prototype.trim=function(){return this."
    "replace(/^\\s+|\\s+$/g,'');};}"
    "if(!Date.now){Date.now=function(){return +new Date();};}"
    "if(!Object.keys){Object.keys=function(o){var r=[];for(var k in o){if("
    "Object.prototype.hasOwnProperty.call(o,k)){r.push(k);}}return r;};}"
    "if(!Function.prototype.bind){Function.prototype.bind=function(t){var f="
    "this,a=Array.prototype.slice.call(arguments,1);return function(){return "
    "f.apply(t,a.concat(Array.prototype.slice.call(arguments)));};};}"
    "if(!Array.prototype.forEach){Array.prototype.forEach=function(f){for("
    "var i=0;i<this.length;i++){f(this[i],i,this);}};}"
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
    """Best-effort const/let -> var pass, preserving literals and comments.

    This is not a transpiler; unsupported syntax remains unsupported.
    """
    # Preserve literal/comment text. Arrow functions need lexical this and
    # arguments semantics, which a regex substitution cannot preserve.
    tokens = re.compile(
        r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|'''
        r'''/\*.*?\*/|//[^\r\n]*|/(?![/*])(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\r\n\\])+/[a-z]*)''',
        re.S)
    parts = tokens.split(src)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\b(?:const|let)\s+(?=[A-Za-z_$])", "var ", parts[i])
    return "".join(parts)

SKIP_URL_PREFIXES = ("data:", "#", "javascript:", "mailto:", "tel:", "about:")


def document_styles(html, base):
    """Return the document base and stylesheet sources in DOM order.

    HTMLParser keeps comments, script strings and quoted '>' characters
    from being mistaken for stylesheet elements. Attributes are decoded
    once here, including unquoted hrefs and entity-escaped query strings.
    """
    class Sources(HTMLParser):
        CDATA_CONTENT_ELEMENTS = ("script", "style", "textarea", "title")

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.base = None
            self.sources = []
            self.style = None
            self.template_depth = 0

        def handle_starttag(self, tag, pairs):
            attrs = dict(pairs)
            if tag == "template":
                self.template_depth += 1
            if self.template_depth:
                return
            if tag == "base" and self.base is None and "href" in attrs:
                self.base = urljoin(base, attrs["href"] or "")
            if "disabled" in attrs or not _media_query_true(attrs.get("media") or ""):
                return
            if (attrs.get("type") or "text/css").lower() != "text/css":
                return
            if tag == "style":
                self.style = ["inline", ""]
                self.sources.append(self.style)
            if tag == "link":
                rel = (attrs.get("rel") or "").lower().split()
                if "stylesheet" in rel and "alternate" not in rel and attrs.get("href"):
                    self.sources.append(["external", attrs["href"]])

        def handle_endtag(self, tag):
            if tag == "style":
                self.style = None
            if tag == "template":
                self.template_depth = max(0, self.template_depth - 1)

        def handle_data(self, data):
            if self.style is not None:
                self.style[1] += data

    parser = Sources()
    parser.feed(html)
    parser.close()
    return parser.base or base, parser.sources


def _rewrite_attrs(tag, base, abs_base=None):
    def sub(a):
        name = a.group(1).lower()
        val = unescape(next(g for g in a.groups()[1:] if g is not None))
        if name == "srcset":
            return ""
        if name == "style":
            return 'style="%s"' % escape(_rewrite_css_urls(
                _transform_decls(val), base, abs_base), quote=True)
        if name == "media" and re.match(r"<(?:link|style)\b", tag, re.I):
            return 'media="%s"' % ("screen" if _media_query_true(val) else "print")
        # src/href/poster are the usual URL attributes; resource and
        # content are MediaWiki's lazy-load URL attributes (IE6 never runs
        # the lazy-load JS, but the URLs should still be http).
        if name in ("src", "href", "poster", "action", "background",
                    "resource", "content") and val:
            v = val.strip()
            if v.lower().startswith(SKIP_URL_PREFIXES):
                return a.group(0)
            # resource/content hold arbitrary text on <meta> (descriptions,
            # viewport...): only rewrite them when they are really URLs.
            if name in ("resource", "content") \
                    and not re.match(r"(?:https?:)?//", v, re.I):
                return a.group(0)
            absu = base if name == "href" and re.match(r"<base\b", tag, re.I) else urljoin(base, v)
            if urlparse(absu).scheme in ("http", "https"):
                v = to_proxy(absu, abs_base)
            return '%s="%s"' % (a.group(1), escape(v, quote=True))
        return a.group(0)

    return ATTR_RE.sub(sub, tag)


def transform_html(html, base, abs_base=None):
    # <base href> changes how relative URLs resolve -- honour it first.
    base, _ = document_styles(html, base)

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
        # Only touch real JavaScript: JSON-LD, text/template etc. contain
        # prose like "let us know" that the const/let rewrite would mangle.
        tm = re.search(r"\btype\s*=\s*['\"]?([^'\"\s>]+)", open_tag, re.I)
        if tm and "javascript" not in tm.group(1).lower() \
                and "ecmascript" not in tm.group(1).lower():
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
        # Drop the whole attribute, including an optional ="value"; leaving
        # a bare "=" behind produces <script=""> whose empty attribute name
        # breaks HTML5 parsers (the script gets moved out of <head>).
        return re.sub(r"\s(?:defer|async)(?:\s*=\s*[^>\s]*)?", "", m.group(0))

    html = re.sub(r"<script\b[^>]*>", _clean_script_open, html, flags=re.I)

    html, restore_raw = protect_raw_text(html)

    # <picture>: keep the fallback <img>, drop <source>s.
    html = re.sub(r"<picture(\s[^>]*)?>", r"<div\1>", html, flags=re.I)
    html = re.sub(r"</picture\s*>", "</div>", html, flags=re.I)
    html = re.sub(r"<source\b[^>]*>", "", html, flags=re.I)
    # <template>: inert in modern browsers, but IE6 renders its contents
    # verbatim -- drop the whole element.
    html = re.sub(r"<template\b[^>]*>.*?</template\s*>", "", html,
                  flags=re.I | re.S)

    # Inline <svg>: IE6 ignores the unknown element but renders its text
    # children (<title>, <text>) as stray page text -- drop it.
    html = re.sub(r"<svg\b[^>]*>.*?</svg\s*>", "", html, flags=re.I | re.S)

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

    # Standards-mode doctype + explicit charset for IE6.  IE6 only enters
    # standards mode when the DOCTYPE is the very first thing in the file: an
    # XML prolog, BOM, comment or whitespace before it forces quirks mode.
    html = html.lstrip("\ufeff \t\r\n")
    html = re.sub(r"^<\?xml[^>]*\?>\s*", "", html, flags=re.I)
    if re.search(r"<!doctype[^>]*>", html, re.I):
        html = re.sub(r"^.*?(?=<!doctype)", "", html, count=1,
                      flags=re.I | re.S)
        html = re.sub(r"<!doctype[^>]*>", XHTML_STRICT_DOCTYPE, html,
                      count=1, flags=re.I)
    else:
        html = XHTML_STRICT_DOCTYPE + "\n" + html
    # The proxy always re-encodes the page as UTF-8, so any charset the page
    # declares must be rewritten to match (also expanding the HTML5
    # <meta charset> shorthand, which IE6 does not understand).
    html = re.sub(
        r"<meta\s+charset\s*=\s*(['\"]?)[^'\"\s>/]+\1\s*/?>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        html, flags=re.I)
    html = re.sub(
        r"(<meta\b[^>]*content\s*=\s*['\"][^'\"]*?charset\s*=\s*)[\w-]+",
        r"\1utf-8", html, flags=re.I)
    if not re.search(r"<meta[^>]+charset", html, re.I):
        ins = '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
        if re.search(r"<head\b[^>]*>", html, re.I):
            html = re.sub(r"(<head\b[^>]*>)", r"\1" + ins, html, count=1, flags=re.I)
        elif re.search(r"<html\b[^>]*>", html, re.I):
            html = re.sub(r"(<html\b[^>]*>)", r"\1" + ins, html, count=1, flags=re.I)

    # ES5 compatibility shims, before any page script can run.
    if re.search(r"<head\b[^>]*>", html, re.I):
        html = re.sub(
            r"(<head\b[^>]*>)", lambda m: m.group(1) + IE6_SHIM,
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

    # IE6 31-stylesheet-limit fix: inject after the <link> tags (i.e. just
    # before </head>) so it runs once every stylesheet is in the DOM.  The
    # src points at the proxy's own internal path -- absolute in
    # system-proxy mode (the page is served as if from the origin host),
    # relative otherwise.  Done after the attribute rewrite so the src is
    # not re-wrapped in the proxy path scheme.
    if re.search(r"</head\s*>", html, re.I):
        script = '<script src="%s"></script>' % (
            (abs_base or "") + FIX_IE_CSS_LIMIT_PATH
        )
        html = re.sub(
            r"</head\s*>", lambda m: script + m.group(0),
            html, count=1, flags=re.I,
        )
    return restore_raw(html)
