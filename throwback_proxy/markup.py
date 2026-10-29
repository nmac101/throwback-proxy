"""Protect non-markup regions while applying legacy HTML rewrite passes."""

import re
from html.parser import HTMLParser


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
    "hgroup": "div",
    "search": "div",
    "data": "span",
}

def protect_raw_text(html, tags=("script", "style", "textarea", "title")):
    """Mask comments and raw/RCDATA bodies, returning text and a restorer.

    Offsets come from HTMLParser so quoted '>' characters are safe. Keep
    element start/end tags available to the URL and layout rewriters.
    """
    spans = []
    lines = [0] + [m.end() for m in re.finditer("\n", html)]

    class Parser(HTMLParser):
        CDATA_CONTENT_ELEMENTS = ("script", "style", "textarea", "title")

        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.raw_start = None

        def source_offset(self):
            line, column = self.getpos()
            return lines[line - 1] + column

        def handle_starttag(self, tag, attrs):
            if tag in tags:
                self.raw_start = self.source_offset() + len(self.get_starttag_text())

        def handle_endtag(self, tag):
            if tag in self.CDATA_CONTENT_ELEMENTS and self.raw_start is not None:
                spans.append((self.raw_start, self.source_offset()))
                self.raw_start = None

        def handle_comment(self, data):
            start = self.source_offset()
            end = html.find("-->", start)
            if end != -1:
                spans.append((start, end + 3))

    parser = Parser()
    parser.feed(html)
    parser.close()
    if parser.raw_start is not None:
        spans.append((parser.raw_start, len(html)))
    prefix = "\x00ie6raw"
    while prefix in html:
        prefix += "x"
    saved = {}
    for index, (start, end) in enumerate(sorted(spans, reverse=True)):
        token = "%s%d\x00" % (prefix, index)
        saved[token] = html[start:end]
        html = html[:start] + token + html[end:]
    pattern = re.compile(re.escape(prefix) + r"\d+\x00")

    def restore(text):
        return pattern.sub(lambda m: saved[m.group(0)], text)

    return html, restore
