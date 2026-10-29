"""Layout fixes applied to rewritten HTML: flex/grid rows approximated with
inline-block/floats, position:fixed elements pinned by a JS scroll handler,
viewport-percentage sizing, background images, and bottom-pinned elements."""

import re
from html import unescape as html_unescape
from urllib.parse import urljoin

from .css import _strip_at_blocks
from .urls import to_proxy
from .flex import (FlexClasses, _FLEX_ROW_CHILD_TAGS, _VOID_TAGS,
    _ancestor_map, _apply_edits, _apply_style_edits, _apply_tag_edits,
    _clearfix_floated_parents, _fixed_px_widths, _flex_container_classes,
    _flex_row_layout, _flex_sibling_floats, _float_classes,
    _full_width_classes, _grid_column_classes, _grid_row_layout,
    _pinned_classes, _row_container_widths, _with_style)



# Use a scroll handler to avoid the repeated evaluation cost of CSS
# expressions (see reference PERF-EXPRESSIONS). Elements are found by class (from the CSS)
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
    page scrolls without CSS expressions."""
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
    "var running=false,lastW,lastH;"
    "function px(v){var n=parseInt(v,10);return isNaN(n)?0:n;}"
    "function size(){"
    "var dl=document.documentElement,b=document.body;"
    "var h=(dl&&dl.clientHeight)||b.clientHeight||768;"
    "var w=(dl&&dl.clientWidth)||b.clientWidth||1024;"
    "if(running||(w==lastW&&h==lastH))return;"
    "running=true;lastW=w;lastH=h;try{"
    "for(var i=0;i<els.length;i++){"
    "var e=document.getElementById(els[i].id);if(!e)continue;"
    "var cs=e.currentStyle;"
    "var pad=els[i].p=='height'"
    "?px(cs.paddingTop)+px(cs.paddingBottom)"
    ":px(cs.paddingLeft)+px(cs.paddingRight);"
    "var v=els[i].f*(els[i].p=='height'?h:w)-pad;"
    "if(v>0){e['_tbViewport'+els[i].p]=true;"
    "if(e.style[els[i].p]!=v+'px')e.style[els[i].p]=v+'px';}}}"
    "finally{running=false;}}"
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
            for part in rm.group(1).split(","):
                cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
                if cm:
                    vp.setdefault(cm[-1].group(1), []).append(
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
    """Class -> (url, size) for rules with a background image AND
    background-size (cover/contain/100%).  IE6 can't scale background
    images (and a width:100%-ish div with no height collapses to nothing),
    so the HTML step replaces the image with a nested <img> that scales
    like the requested size."""
    # Only top-level rules survive transform_css (it strips @media/@supports),
    # so scan the same subset -- otherwise a mobile media-query rule (e.g. a
    # small icon logo) clobbers the desktop one.
    css = _strip_at_blocks(css, ["media", "supports"])
    out = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        body = rm.group(2)
        sm = re.search(
            r"background-size\s*:\s*(cover|contain|100%|"
            r"(\d+)px(?:\s+(\d+)px)?)",
            body, re.I)
        if not sm:
            continue
        bm = re.search(
            r"(?<![\w-])background(?:-image)?\s*:\s*[^;]*?url\(\s*(['\"]?)(.*?)\1\s*\)",
            body, re.I,
        )
        if not bm:
            continue
        # explicit px geometry: IE6 mishandles percentage widths on <img>
        # inside floats, so the injected img gets pixel dimensions.  An
        # explicit "background-size: Wpx Hpx" wins over the rule's own
        # width/height (the two often differ, e.g. a 190px logo in a wider
        # box).
        bs_w = int(sm.group(2)) if sm.group(2) else None
        bs_h = int(sm.group(3)) if sm.group(3) else None
        wm = re.search(r"(?<![\w-])width\s*:\s*(\d+)px", body, re.I)
        hm = re.search(r"(?<![\w-])height\s*:\s*(\d+)px", body, re.I)
        entry = (bm.group(2), sm.group(1).lower(),
                 bs_w or (int(wm.group(1)) if wm else None),
                 bs_h or (int(hm.group(1)) if hm else None))
        # only the LAST class/id of the selector (most specific) -- mapping
        # ancestor classes too would inject the img into their elements
        cls = re.findall(r"\.([A-Za-z_][\w-]*)", rm.group(1))
        if cls:
            out[cls[-1]] = entry
        ids = re.findall(r"#([A-Za-z_][\w-]*)", rm.group(1))
        if ids:
            out["#" + ids[-1]] = entry
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
        hit = None
        idm = re.search(r'id\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if idm and ("#" + idm.group(1)) in bg:
            hit = bg["#" + idm.group(1)]
        if not hit:
            cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
            if cm:
                for cls in set(cm.group(1).split()):
                    if cls in bg:
                        hit = bg[cls]
                        break
        if not hit:
            continue
        raw_url, size, px_w, px_h = hit
        img_url = to_proxy(urljoin(base, html_unescape(raw_url)), abs_base)
        if px_w and img_url.lower().endswith(".svg"):
            # tell the image phase to render the SVG at display width so IE6
            # shows the PNG 1:1 (it paints scaled transparent-PNG alpha black)
            img_url += ("?" if "?" not in img_url else "&") + \
                "ie6w=%d" % px_w
        if px_w:
            img_style = "width:%dpx" % px_w
            if px_h:  # explicit px (or cover) ~ stretch in IE6
                img_style += ";height:%dpx" % px_h
        else:
            img_style = "width:100%"
        tag_text = html[m.start():m.end()]
        if re.search(r"\bstyle\s*=\s*[\"']", tag_text, re.I):
            tag_text = re.sub(
                r"(\bstyle\s*=\s*[\"'])([^\"']*)",
                # the stylesheet background may be !important; inline
                # !important is the only thing that beats it.  It must lose
                # because IE6 paints transparent PNG *backgrounds* black --
                # the injected <img> must be the sole painter.
                lambda mm: mm.group(1) + mm.group(2) +
                           "background-image:none !important;",
                tag_text, count=1, flags=re.I,
            )
        else:
            tag_text = tag_text[:-1] + \
                ' style="background-image:none !important;">'
        img = ('<img src="%s" alt="" '
               'style="display:block;%s;border:0">' % (img_url, img_style))
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



_BLOCK_CONTAINER_TAGS = ("div", "form", "section", "nav", "ul", "header",
                         "article", "aside", "main", "footer")


def _max_width_classes(css):
    """class -> px for rules with a modest `max-width` (200-700px) and no
    width of their own: widget containers such as a search box capped at
    31.25rem.  IE6 has no max-width, so a block holding a width:100% child
    (a text input) would stretch across the whole page."""
    out = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        body = rm.group(2)
        mm = re.search(r"(?<![\w-])max-width\s*:\s*([\d.]+)(px|rem|em)\b",
                       body, re.I)
        if not mm or re.search(r"(?<![\w-])(?:min-)?width\s*:", body, re.I):
            continue
        px = float(mm.group(1)) * (1 if mm.group(2) == "px" else 16)
        if not 200 <= px <= 700:
            continue
        for part in rm.group(1).split(","):
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm:
                out[cm[-1].group(1)] = int(px)
    return out


def _apply_max_widths(html, max_widths, skip=()):
    """Give block containers with a known max-width that width (see above)."""
    if not max_widths:
        return html
    edits = []
    for m in re.finditer(r"<(%s)\b([^>]*)>" % "|".join(_BLOCK_CONTAINER_TAGS),
                         html, re.I):
        attrs = m.group(2)
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm or re.search(r"style\s*=\s*[\"'][^\"']*width", attrs, re.I):
            continue
        cls = cm.group(1).split()
        w = next((max_widths[c] for c in cls
                  if c in max_widths and c not in skip), None)
        if w:
            edits.append((m.start(), m.end(), "width:%dpx;" % w))
    return _apply_tag_edits(html, edits)


def _multicolumn_layout(html, css):
    """Approximate simple newspaper columns with balanced, top-aligned cells.

    Keep each direct child intact (definition lists on ArchWiki, for example).
    Balancing by item count is deterministic; IE6 cannot measure modern CSS
    column heights. Only simple class selectors are safe to infer here.
    """
    from .css import _flatten_css, _resolve_vars

    columns = {}
    for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}',
                            _resolve_vars(_flatten_css(css))):
        count = re.search(r'(?<![\w-])column-count\s*:\s*(auto|\d+)\s*(?:;|$)',
                          rule[2], re.I)
        if not count:
            continue
        for selector in rule[1].split(','):
            match = re.fullmatch(r'\s*(?:[\w-]+)?\.([\w-]+)\s*', selector)
            if match:
                columns[match[1]] = int(count[1]) if count[1].isdigit() else 1
    edits, stack = [], []
    for tag in re.finditer(r'</?([a-zA-Z][\w-]*)\b[^>]*>', html):
        name = tag[1].lower()
        if tag[0].startswith('</'):
            if not stack or stack[-1]['name'] != name:
                continue
            node = stack.pop()
            if stack and stack[-1]['count'] > 1:
                stack[-1]['children'].append((node['start'], tag.end()))
            count, children = node['count'], node['children']
            if count > 1 and len(children) > 1:
                count = min(count, len(children), 8)
                cells = []
                for i in range(count):
                    lo = (len(children) * i + count - 1) // count
                    hi = (len(children) * (i + 1) + count - 1) // count
                    content = html[children[lo][0]:children[hi - 1][1]]
                    cells.append('<td width="%g%%" valign="top" '
                                 'style="padding:0 8px;vertical-align:top">%s</td>'
                                 % (100 / count, content))
                edits.append((node['end'], tag.start(),
                              '<table role="presentation" width="100%" '
                              'cellpadding="0" cellspacing="0" border="0" '
                              'style="table-layout:fixed"><tr>' + ''.join(cells)
                              + '</tr></table>'))
            continue
        if name in _VOID_TAGS or tag[0].endswith('/>'):
            continue
        classes = re.search(r'\bclass\s*=\s*["\']([^"\']*)', tag[0], re.I)
        count = next((columns[c] for c in classes[1].split() if c in columns), 1) if classes else 1
        # Nested column containers are left alone to avoid overlapping edits.
        if any(n['count'] > 1 for n in stack):
            count = 1
        stack.append(dict(name=name, start=tag.start(), end=tag.end(),
                          count=count, children=[]))
    return _apply_edits(html, edits)
