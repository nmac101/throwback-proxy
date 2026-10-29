"""Small, static compatibility layouts for recognizable page components."""

import re
from html import escape
from html.parser import HTMLParser

from .layout import _with_style
from .markup import protect_raw_text


_SKIP_CLASS = 'tb-ie6-skip-hidden'
_TOC_CLASS = 'tb-ie6-toc'
_SKIP_STYLE = (
    '<style type="text/css">.' + _SKIP_CLASS + '{position:absolute !important;'
    'left:-10000px !important;width:1px !important;height:1px !important;'
    'overflow:hidden !important;}</style>'
)
SKIP_LINK_SCRIPT = r"""<script>(function(){
var marker='tb-ie6-skip-hidden';
function bind(link){
    function show(){link.className=(' '+link.className+' ').replace(' '+marker+' ',' ');}
    function hide(){
        if((' '+link.className+' ').indexOf(' '+marker+' ')==-1){link.className+=' '+marker;}
    }
    if(link.attachEvent){link.attachEvent('onfocus',show);link.attachEvent('onblur',hide);}
    else if(link.addEventListener){link.addEventListener('focus',show,false);link.addEventListener('blur',hide,false);}
}
var links=document.getElementsByTagName('a');
for(var i=0;i<links.length;i++){
    if((' '+links[i].className+' ').indexOf(' '+marker+' ')!=-1){bind(links[i]);}
}
})();</script>"""


def _add_class(tag, classes, name):
    value = escape(' '.join(classes + [name]), quote=True)
    return re.sub(r'''\sclass\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)''',
                  lambda m: ' class="' + value + '"', tag, count=1, flags=re.I)


def apply_mediawiki_fallbacks(html):
    """Compact legacy TOCs and keyboard-visible skip links, without sizing JS.

    Scope this adapter to pages identifying themselves as MediaWiki. Native
    table TOCs and unrelated components retain their existing layout.
    """
    html, restore = protect_raw_text(html)
    offsets = [0] + [m.end() for m in re.finditer('\n', html)]
    edits = []

    class Components(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.mediawiki = False
            self.depth = 0
            self.toc = None
            self.skip_links = False

        def position(self):
            row, col = self.getpos()
            return offsets[row - 1] + col

        def handle_starttag(self, tag, pairs):
            attrs = dict(pairs)
            if (tag == 'meta' and (attrs.get('name') or '').lower() == 'generator'
                    and (attrs.get('content') or '').lower().startswith('mediawiki')):
                self.mediawiki = True
            if tag == 'div':
                self.depth += 1
            if not self.mediawiki:
                return
            classes = (attrs.get('class') or '').split()
            original = self.get_starttag_text()
            start = self.position()
            if (tag == 'div' and attrs.get('id') == 'toc' and 'toc' in classes
                    and _TOC_CLASS not in classes):
                self.toc = (self.depth, start, original, classes)
            if tag == 'a' and 'mw-jump-link' in classes and _SKIP_CLASS not in classes:
                edits.append((start, start + len(original),
                              _add_class(original, classes, _SKIP_CLASS)))
                self.skip_links = True

        def handle_endtag(self, tag):
            if tag != 'div':
                return
            if self.toc and self.depth == self.toc[0]:
                _, start, original, classes = self.toc
                new = _add_class(original, classes, _TOC_CLASS)
                new = _with_style(new, ';float:left;width:auto;display:block;')
                edits.append((start, start + len(original), new))
                end = html.find('>', self.position()) + 1
                edits.append((end, end, '<div aria-hidden="true" '
                              'style="clear:left;height:0;line-height:0;font-size:0;'
                              'overflow:hidden;"></div>'))
                self.toc = None
            self.depth = max(0, self.depth - 1)

    parser = Components()
    parser.feed(html)
    parser.close()
    # A clear element can be inserted exactly where the next link starts.
    # Replace that link first, then insert the clear, preserving offsets.
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        html = html[:start] + replacement + html[end:]
    if parser.skip_links:
        head = re.search(r'</head\s*>', html, re.I)
        if head:
            html = html[:head.start()] + _SKIP_STYLE + html[head.start():]
        else:
            body = re.search(r'<body\b[^>]*>', html, re.I)
            at = body.end() if body else 0
            html = html[:at] + _SKIP_STYLE + html[at:]
        close = re.search(r'</body\s*>', html, re.I)
        at = close.start() if close else len(html)
        html = html[:at] + SKIP_LINK_SCRIPT + html[at:]
    return restore(html)
