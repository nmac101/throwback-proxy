"""HTML rewriting: HTML5 tag down-conversion, ES5 script down-grading,
ad/module script removal, and URL rewriting through the proxy."""

import re
from urllib.parse import urljoin, urlparse

from .config import XHTML_STRICT_DOCTYPE
from .css import _rewrite_css_urls, _transform_decls
from .urls import to_proxy

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
    "progress": "span",
    "meter": "span",
    "dialog": "div",
    "ruby": "span",
    "rt": "span",
    "rp": "span",
    "rtc": "span",
    "datalist": "span",
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
            return 'style="%s"' % _rewrite_css_urls(
                _transform_decls(val), base, abs_base
            ).replace('"', "'")
        # src/href/poster are the usual URL attributes; resource and
        # content are MediaWiki's lazy-load URL attributes (IE6 never runs
        # the lazy-load JS, but the URLs should still be http).
        if name in ("src", "href", "poster", "resource", "content") and val:
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
        # Drop the whole attribute, including an optional ="value"; leaving
        # a bare "=" behind produces <script=""> whose empty attribute name
        # breaks HTML5 parsers (the script gets moved out of <head>).
        return re.sub(r"\s(?:defer|async)(?:\s*=\s*[^>\s]*)?", "", m.group(0))

    html = re.sub(r"<script\b[^>]*>", _clean_script_open, html, flags=re.I)

    # <picture>: keep the fallback <img>, drop <source>s.
    html = re.sub(r"<picture(\s[^>]*)?>", r"<div\1>", html, flags=re.I)
    html = re.sub(r"</picture\s*>", "</div>", html, flags=re.I)
    html = re.sub(r"<source\b[^>]*>", "", html, flags=re.I)
    # <template>: inert in modern browsers, but IE6 renders its contents
    # verbatim -- drop the whole element.
    html = re.sub(r"<template\b[^>]*>.*?</template\s*>", "", html,
                  flags=re.I | re.S)

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
    # IE6 does not understand the HTML5 <meta charset> shorthand --
    # rewrite it to the http-equiv form it does.
    html = re.sub(
        r"<meta\s+charset\s*=\s*(['\"])([^'\"]+)\1\s*/?>",
        r'<meta http-equiv="Content-Type" content="text/html; charset=\2">',
        html, flags=re.I)
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
