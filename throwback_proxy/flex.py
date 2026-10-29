"""Flex/grid row approximation: inline-block/float layouts, clearfix, and the
shared tag/style edit helpers."""

import re

from .urls import to_proxy

class FlexClasses(set):
    """Row-flex container classes found in a page's CSS.

    The set itself holds classes that are flex containers wherever they
    appear.  `scoped` maps a class to the ancestor requirements of rules like
    ".user-menu .list {display:flex}": there, only a .list *inside* a
    .user-menu is a flex row -- other .list elements (e.g. a sidebar menu
    sharing the same component class) are plain blocks."""

    def __init__(self):
        super().__init__()
        self.scoped = {}  # class -> [frozenset(ancestor tokens), ...]
        self.justify = {}  # class -> justify-content value
        self.grow = set()  # classes with flex-grow >= 1 (fill the row)

    def __bool__(self):
        return bool(len(self) or self.scoped)

    def merge(self, other):
        self.update(other)
        for k, v in other.scoped.items():
            self.scoped.setdefault(k, []).extend(v)
        self.justify.update(other.justify)
        self.grow |= other.grow

    def justify_of(self, classes):
        for c in classes:
            if c in self.justify:
                return self.justify[c]
        return None

    def matches(self, classes, ancestors):
        """Is an element with *classes* inside *ancestors* a flex row?
        (*ancestors*: class names and "#id" tokens of its ancestors.)"""
        if classes & self:
            return True
        for c in classes:
            for req in self.scoped.get(c, ()):
                if req <= ancestors:
                    return True
        return False


def _flex_container_classes(css):
    """Row-direction flex containers (the ones that lay their children out
    side by side), as a FlexClasses. Column flex degrades fine to plain
    block."""
    classes = FlexClasses()
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"(?<![\w-])flex(?:-grow)?\s*:\s*[1-9]", rm.group(2),
                     re.I):
            for part in rm.group(1).split(","):
                gm = list(re.finditer(r"([.#])([A-Za-z_][\w-]*)", part))
                if gm:  # ids are kept with their "#" (classes can't clash)
                    classes.grow.add(("#" if gm[-1].group(1) == "#" else "")
                                     + gm[-1].group(2))
    # Later rules override earlier ones (".mw-header{display:flex}" ...
    # ".mw-header{display:grid}" is a grid), so keep the last display set
    # for each selector part.
    state = {}  # selector part -> (is-row-flex, rule body)
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        dm = re.search(r"(?<![\w-])display\s*:\s*([\w-]+)", body, re.I)
        if not dm:
            continue
        is_flex = (dm.group(1).lower() in ("flex", "inline-flex")
                   and not re.search(r"flex-direction\s*:\s*column", body,
                                     re.I))
        for part in sel.split(","):
            state[part.strip()] = (is_flex, body)
    for part, (is_flex, body) in state.items():
        if is_flex:
            # Only the last class in each selector part is the flex
            # container; earlier classes are ancestor components (e.g.
            # ".mw-header .vector-header-end" is the end row, not .mw-header).
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if not cm:
                continue
            last = cm[-1]
            jm = re.search(r"justify-content\s*:\s*([\w-]+)", body, re.I)
            if jm:
                classes.justify[last.group(1)] = jm.group(1).lower()
            # classes/ids named before the last compound are ancestors
            cut = max(part.rfind(c, 0, last.start()) for c in " >+~")
            head = re.sub(r":not\([^)]*\)", "", part[:cut + 1])
            req = frozenset(
                t[1:] if t[0] == "." else t
                for t in re.findall(r"\.[A-Za-z_][\w-]*|#[A-Za-z_][\w-]*", head)
            )
            if req:
                classes.scoped.setdefault(last.group(1), []).append(req)
            else:
                classes.add(last.group(1))
    return classes


def _ancestor_map(html):
    """{open-tag start offset: frozenset(class names and "#id" tokens of the
    element's ancestors)}.  A light stack walk: end tags pop back to the
    nearest open element of the same name, so optional end tags (<li>, <p>)
    don't derail it."""
    out = {}
    stack = []  # (tag, cumulative tokens including this element's own)
    cur = frozenset()
    for m in re.finditer(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        tag, attrs = m.group(1).lower(), m.group(2) or ""
        if m.group(0)[1] == "/":
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    del stack[i:]
                    cur = stack[-1][1] if stack else frozenset()
                    break
            continue
        out[m.start()] = cur
        if tag in _VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        toks = set()
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if cm:
            toks.update(cm.group(1).split())
        im = re.search(r'\bid\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if im:
            toks.add("#" + im.group(1))
        cur = cur | toks if toks else cur
        stack.append((tag, cur))
    return out


_FLEX_ROW_CHILD_TAGS = set(
    "div section article aside nav main header footer ul ol li table form "
    "fieldset a span".split()
)
_VOID_TAGS = set(
    "area base br col embed hr img input link meta param source track wbr".split()
)


def _grid_column_classes(css):
    """display:grid containers whose FIRST column track is a simple fixed
    length (e.g. `grid-template-columns:12.25rem minmax(0,1fr)` -- the
    classic fixed-sidebar + fluid-content pattern).

    Returns (containers, area_classes):
      containers:   container class -> {"width": px, "area": area-name or None}
                    "area" is the grid-template-areas name occupying the
                    first column (the child that belongs in the left column).
      area_classes: grid-area name -> set of classes that declare it.
    Used to float the right child into a left column for IE6."""
    containers = {}
    area_classes = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        am = re.search(r"grid-area\s*:\s*([A-Za-z_-]+)", body, re.I)
        if am:
            for part in sel.split(","):
                cm3 = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
                if cm3:
                    area_classes.setdefault(am.group(1).lower(),
                                            set()).add(cm3[-1].group(1))
        names = []
        for part in sel.split(","):
            cm2 = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm2:
                names.append(cm2[-1].group(1))
        # a later rule may re-declare the tracks of an earlier grid
        # (`.x{display:grid;grid-template:..} .x{grid-template-columns:..}`)
        if not (re.search(r"display\s*:\s*grid\b", body, re.I)
                or any(n in containers for n in names)):
            continue
        cols = None
        cm = re.search(r"grid-template-columns\s*:\s*([^;]+)", body, re.I)
        if cm:
            cols = cm.group(1)
        else:
            tm = re.search(r"grid-template\s*:\s*([^;]+)", body, re.I)
            if tm and "/" in tm.group(1):
                cols = tm.group(1).rsplit("/", 1)[1]
        if not cols:
            continue
        tracks = cols.strip().split()
        if not tracks:
            continue
        fm = re.match(r"^(\d+(?:\.\d+)?)(px|rem|em)$", tracks[0])
        if not fm:
            continue
        val = float(fm.group(1))
        if fm.group(2) in ("rem", "em"):
            val *= 16
        w = int(val)
        if not 40 <= w <= 700:
            continue
        # The area name in column 1: first quoted row of grid-template-areas
        # whose cells differ (rows where every cell is the same name span all
        # columns, e.g. 'siteNotice siteNotice').
        area = None
        am2 = re.search(r"grid-template-areas\s*:\s*([^;]+)", body, re.I)
        if am2:
            for row in re.findall(r"'([^']*)'", am2.group(1)):
                cells = row.split()
                if len(cells) >= 2 and cells[0] != cells[1]:
                    area = cells[0].lower()
                    break
        for n in names:
            old = containers.get(n)
            containers[n] = {"width": w,
                             "area": area or (old["area"] if old else None)}
    return containers, area_classes


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
        for part in sel.split(","):
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm:
                widths.setdefault(cm[-1].group(1), w)
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
        for part in rm.group(1).split(","):
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm:
                pinned.add(cm[-1].group(1))
    return pinned


def _full_width_classes(css):
    """Class names of simple rules that set width:100% (full-bleed blocks).
    Floating one of these consumes the whole line, which shrinks the
    available width of the next block-level box to zero (CSS 2.1 9.5.1) --
    e.g. a full-width header float:left collapses the page grid that
    follows it."""
    names = set()
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        if not re.search(r"(?<![-\w])width\s*:\s*100%\s*(?:;|$)", body, re.I):
            continue
        for part in sel.split(","):
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm:
                names.add(cm[-1].group(1))
    return names


def _row_container_widths(css):
    """class name -> fixed pixel width (any size, 40..2000px) from simple
    class rules.  Unlike _fixed_px_widths (capped at 700px, for sidebar
    columns) this captures full-width row containers, so an equal-width
    (flex:1) row can be sized to fill its container in IE6."""
    widths = {}
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        wm = re.search(r"(?<![\w-])width\s*:\s*(\d+)px", body, re.I)
        if not wm:
            continue
        w = int(wm.group(1))
        if not 40 <= w <= 2000:
            continue
        for part in sel.split(","):
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm:
                widths.setdefault(cm[-1].group(1), w)
    return widths


def _flex_sibling_floats(html, flex_classes, full_width=None):
    """Float flex containers that are siblings of other flex containers.
    Handles the common 'row of sub-rows' pattern (e.g. a header with a left
    group and a right group, each a flex row) where the parent is a plain
    block and the sub-rows would otherwise stack vertically.  Only applies
    when the parent itself is not a flex container (that case is handled by
    _flex_row_layout's child pass).  Full-width (width:100%) flex containers
    are excluded: they can't share a line with a sibling, and floating them
    collapses the width of the content that follows."""
    if not flex_classes:
        return []
    full_width = full_width or set()
    anc = _ancestor_map(html) if flex_classes.scoped else {}
    els = []  # {start, end, flex, cls, parent}
    open_stack = []  # (tag, index_in_els)
    for m in re.finditer(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        closing, tag, attrs = m.group(0)[1] == "/", m.group(1).lower(), m.group(2) or ""
        if closing:
            if open_stack and open_stack[-1][0] == tag:
                open_stack.pop()
            continue
        if tag in _VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        parent = open_stack[-1][1] if open_stack else -1
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        cls = set(cm.group(1).split()) if cm else set()
        idx = len(els)
        els.append({"start": m.start(), "end": m.end(),
                    "flex": flex_classes.matches(
                        cls, anc.get(m.start(), frozenset())),
                    "cls": cls,
                    "parent": parent})
        open_stack.append((tag, idx))
    by_parent = {}
    for e in els:
        by_parent.setdefault(e["parent"], []).append(e)
    edits = []
    for parent, kids in by_parent.items():
        flex_kids = [k for k in kids
                     if k["flex"] and not (k["cls"] & full_width)]
        if len(flex_kids) >= 2 and (parent < 0 or not els[parent]["flex"]):
            for k in flex_kids:
                edits.append((k["start"], k["end"], "float:left;"))
            if parent >= 0:
                edits.append((els[parent]["start"], els[parent]["end"],
                              "overflow:hidden;"))
    return edits


def _float_classes(css):
    """Class/id names of the last selector component in each rule that sets
    float:left/right (e.g. a navbar logo float:left and menu float:right)."""
    names = set()
    for rm in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = rm.group(1), rm.group(2)
        if not re.search(r"(?<![-\w])float\s*:\s*(?:left|right)\b", body, re.I):
            continue
        for part in sel.split(","):
            cm = list(re.finditer(r"\.([A-Za-z_][\w-]*)", part))
            if cm:
                names.add(cm[-1].group(1))
                continue
            im = list(re.finditer(r"#([A-Za-z_][\w-]*)", part))
            if im:
                names.add(im[-1].group(1))
    return names


def _clearfix_floated_parents(html, float_classes):
    """Add overflow:hidden (clearfix) to elements that contain a floated
    child (matched by class/id) but don't already clear it.  In IE6 a block
    with floated children and no BFC lets the *next* sibling flow around the
    floats -- e.g. a navbar with a float:left logo and float:right menu makes
    the following header wrap up into the navbar's top-right corner."""
    if not float_classes:
        return []
    els = []  # {start, end, cls, id, parent, has_oh}
    open_stack = []  # (tag, index_in_els)
    for m in re.finditer(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        closing, tag, attrs = m.group(0)[1] == "/", m.group(1).lower(), m.group(2) or ""
        if closing:
            if open_stack and open_stack[-1][0] == tag:
                open_stack.pop()
            continue
        if tag in _VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        parent = open_stack[-1][1] if open_stack else -1
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        im = re.search(r'id\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        cls = set(cm.group(1).split()) if cm else set()
        idn = im.group(1) if im else ""
        sm = re.search(r'style\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        has_oh = bool(sm and re.search(r"overflow\s*:\s*hidden", sm.group(1), re.I))
        idx = len(els)
        els.append({"start": m.start(), "end": m.end(),
                    "cls": cls, "id": idn, "parent": parent, "has_oh": has_oh})
        open_stack.append((tag, idx))
    by_parent = {}
    for i, e in enumerate(els):
        by_parent.setdefault(e["parent"], []).append(i)
    edits = []
    for i, e in enumerate(els):
        if e["has_oh"]:
            continue
        kids = by_parent.get(i, [])
        if any((els[k]["cls"] & float_classes) or (els[k]["id"] in float_classes)
               for k in kids):
            edits.append((e["start"], e["end"], "overflow:hidden;"))
    return edits


def _apply_style_edits(html, edits):
    """Apply a list of (open_start, open_end, css-to-append) edits to the
    HTML, appending css to each target tag's style attribute (creating one if
    absent). Edits are applied back-to-front so earlier offsets stay valid.

    (Distinct from _apply_edits below, which replaces the span with the given
    text -- the two must not share a name, or the later def shadows this one
    and opening tags get clobbered by bare CSS snippets.)"""
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


def _flex_row_layout(html, flex_classes, widths, pinned=None,
                     full_width=None, row_widths=None):
    """IE6 has no flexbox. For each row-flex container element found in the
    HTML: children with a known fixed pixel width (e.g. a fixed-width sidebar)
    float:left (height:auto so the float box wraps all their content); their
    siblings get overflow:hidden so they wrap around the float and take the
    remaining width -- the classic pre-flex two-column pattern, rock solid in
    IE6. If such a child is also position:sticky/fixed (pinned), it becomes
    position:absolute instead (floats can't stay put while scrolling; the
    JS pin script keeps them in place) and its siblings get
    margin-left equal to its outer width. If no child has a known width, the
    row is equal-width (flex:1): if the container has a known fixed width the
    children are sized to fill it evenly, otherwise they float:left at their
    natural width."""
    if not flex_classes:
        return html
    pinned = pinned or set()
    full_width = full_width or set()
    row_widths = row_widths or {}
    edits = []  # (open_start, open_end, css-to-append)
    anc = _ancestor_map(html) if flex_classes.scoped else {}
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        attrs = m.group(2) or ""
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm:
            continue
        if not flex_classes.matches(set(cm.group(1).split()),
                                    anc.get(m.start(), frozenset())):
            continue
        children = []  # (start, end, tag, classes-set, has-inline-display)
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
                cid = re.search(r'\bid\s*=\s*["\']([^"\']*)["\']', tattrs, re.I)
                children.append((
                    start + tm.start(),
                    start + tm.end(),
                    tag,
                    (set(ccm.group(1).split()) if ccm else set())
                    | ({"#" + cid.group(1)} if cid else set()),
                    bool(sm and re.search(r"display", sm.group(1), re.I)),
                ))
            depth += 1
        if not children:
            continue
        has_floated = any(
            (not cd) and any(k in widths for k in cclasses)
            for _, _, _, cclasses, cd in children
        )
        pinned_w = next(
            (widths[k] for _, _, _, ccs, cd in children
             if not cd for k in ccs if k in pinned and k in widths),
            None,
        )
        all_float = not has_floated and pinned_w is None
        # Equal-width (flex:1) row: if the container has a known fixed width,
        # size each child to fill it evenly -- IE6 has no flex:1, so without
        # this the row of links hugs the left at its natural (content) width.
        ew = None
        if all_float:
            for k in cm.group(1).split():
                if k in row_widths:
                    ew = row_widths[k] // len(children)
                    break
        # justify-content pushes the last item to the far end of the row
        # (space-between header: logo left, tools right; flex-end title bar).
        # IE6 can't, so the last child is absolutely positioned at the right
        # edge of a relatively positioned container.
        jc = flex_classes.justify_of(cm.group(1).split())
        tail = None
        if all_float and not ew and (
                (jc == "space-between" and len(children) >= 2)
                or (jc in ("flex-end", "end", "right"))):
            tail = children[-1][0]
            if jc in ("flex-end", "end", "right") and len(children) >= 2:
                # a right-aligned group: float each child right (visual order
                # within the group is reversed, the cost of floating)
                tail = None
                for cs, ce, ctag, cclasses, cd in children:
                    if not cd:
                        edits.append((cs, ce, "float:right;" + (
                            "display:block;" if ctag == "li" else "")))
                children = []
        grow_fill = grow_tail = None
        if all_float and not ew and not tail and len(children) >= 2:
            gi = next((i for i, c in enumerate(children)
                       if c[3] & flex_classes.grow), None)
            if gi == len(children) - 1:
                grow_fill = children[gi][0]
            elif gi == 0 and len(children) == 2 and not children[1][4]:
                grow_tail = children[1][0]
        for cs, ce, ctag, cclasses, cd in children:
            if cd:  # keep explicit inline display untouched
                continue
            if grow_fill == cs:
                # flex-grow child: fills the space left beside the floated
                # siblings (a block formatting context shrinks to fit them)
                edits.append((cs, ce, "overflow:hidden;" + (
                    "zoom:1;" if ctag != "li" else "")))
                continue
            if grow_tail == cs:
                # [flex-grow child, one trailing child]: pin the trailing
                # child to the right edge instead of floating it
                edits.append((cs, ce, "position:absolute;right:0;top:0;"))
                edits.append((m.start(), m.end(), "position:relative;zoom:1;"))
                continue
            if cs == tail:
                edits.append((cs, ce, "position:absolute;right:0;top:0;"))
                edits.append((m.start(), m.end(), "position:relative;zoom:1;"))
                continue
            # IE6 bug: a floated <li> (display:list-item) collapses to a
            # few characters wide, wrapping its text one word/character at a
            # time (e.g. MediaWiki Vector sidebar menus).  display:block
            # restores the normal shrink-to-fit float width.
            li_fix = "display:block;" if ctag == "li" else ""
            if pinned_w is not None and any(k in pinned for k in cclasses):
                add = "position:absolute;left:0;"
            elif has_floated and any(k in widths for k in cclasses):
                add = "float:left;height:auto;" + li_fix
            elif pinned_w is not None:
                add = "margin-left:%dpx;" % pinned_w
            elif has_floated:
                add = "overflow:hidden;"
            elif ew:
                add = "float:left;width:%dpx;" % ew
            else:
                # No child has a known width: float them left so the row's
                # block-level items flow horizontally (display:inline would
                # leave them stacked, since their content is block-level).
                add = "float:left;" + li_fix
            edits.append((cs, ce, add))
        if all_float:
            # clearfix on the container so it wraps the floated children
            edits.append((m.start(), m.end(), "overflow:hidden;"))
    edits.extend(_flex_sibling_floats(html, flex_classes, full_width))
    # _apply_tag_edits, not _apply_style_edits: the child pass and
    # _flex_sibling_floats can both target the same element (a flex
    # container that is itself a child of a row), and applying two edits
    # to one tag out of order slices the grown tag at the original
    # offsets, leaking a bare style="..."> fragment into the page text.
    return _apply_tag_edits(html, edits)


def _apply_tag_edits(html, edits):
    """Apply (open_start, open_end, css-to-append) edits to opening tags,
    appending to an existing style="" or creating one. Edits that target the
    same element (e.g. a flex container that is itself a floated child of
    another flex container) are merged so we don't emit two style=""
    attributes."""
    merged = {}
    for open_start, open_end, add in edits:
        key = (open_start, open_end)
        merged[key] = merged.get(key, "") + add
    edits = [(s, e, st) for (s, e), st in merged.items()]
    return _apply_style_edits(html, edits)


def _apply_edits(html, edits):
    """Apply (start, end, replacement) spans to html, outermost-first so
    inner offsets stay valid."""
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        html = html[:start] + repl + html[end:]
    return html


def _grid_row_layout(html, grid_containers, grid_areas):
    """IE6 has no CSS grid. For each grid container whose first column has a
    known fixed width, float the child belonging to that column (per
    grid-template-areas, else the first non-empty block child) left with that
    width and give the container overflow:hidden -- the same pre-flex
    two-column pattern _flex_row_layout uses."""
    if not grid_containers:
        return html
    edits = []
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", html):
        attrs = m.group(2) or ""
        cm = re.search(r'class\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        if not cm:
            continue
        spec = next((grid_containers[c] for c in cm.group(1).split()
                     if c in grid_containers), None)
        if spec is None:
            continue
        w = spec["width"]
        area_classes = (grid_areas.get(spec["area"]) if spec["area"]
                        else None) or set()
        # First NON-EMPTY block-level direct child becomes the left column
        # (the first child may be a placeholder like an empty site-notice
        # container that spans all grid columns).
        start = m.end()
        depth = 1
        first = None
        after = []  # depth-1 children following the left column
        for tm in re.finditer(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)?>",
                              html[start:]):
            tag, tattrs = tm.group(1).lower(), tm.group(2) or ""
            if tm.group(0)[1] == "/":  # closing tag
                depth -= 1
                if depth == 0:
                    break
                continue
            if tag in _VOID_TAGS or tattrs.rstrip().endswith("/"):
                continue
            if first is not None:
                if depth == 1 and tag in _FLEX_ROW_CHILD_TAGS:
                    after.append((start + tm.start(), start + tm.end()))
            elif depth == 1 and tag in _FLEX_ROW_CHILD_TAGS:
                ccm = re.search(r'class\s*=\s*["\']([^"\']*)["\']',
                                tattrs, re.I)
                cclasses = set(ccm.group(1).split()) if ccm else set()
                # Preferred: the child mapped to the first-column area.
                if cclasses & area_classes:
                    first = (start + tm.start(), start + tm.end())
                    depth += 1
                    continue
                # Fallback: first child with actual content (the first child
                # may be a placeholder like an empty site-notice container
                # that spans all grid columns).
                cdepth = 1
                cend = None
                for tm2 in re.finditer(
                        r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)?>",
                        html[start + tm.end():]):
                    t2 = tm2.group(1).lower()
                    if tm2.group(0)[1] == "/":
                        cdepth -= 1
                        if cdepth == 0:
                            cend = start + tm.end() + tm2.end()
                            break
                        continue
                    if t2 in _VOID_TAGS or tm2.group(2).rstrip().endswith("/"):
                        continue
                    cdepth += 1
                if cend is None:
                    break
                inner = re.sub(r"<[^>]*>", "", html[start + tm.end():cend])
                if inner.strip():
                    first = (start + tm.start(), start + tm.end())
            depth += 1
        if first is None:
            continue
        sm = re.search(r'style\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
        cont_add = "" if (sm and "overflow" in sm.group(1)) else "overflow:hidden;"
        if cont_add:
            edits.append((m.start(), m.end(),
                          _with_style(html[m.start():m.end()], cont_add)))
        edits.append((first[0], first[1],
                      _with_style(html[first[0]:first[1]],
                                  "float:left;width:%dpx;height:auto;" % w)))
        # The content column must be a block formatting context so it takes
        # the space beside the float (else its borders/backgrounds run under
        # the float and only the text is pushed over).
        for a0, a1 in after:
            tag_text = html[a0:a1]
            if not re.search(r"\boverflow\s*:", tag_text, re.I):
                edits.append((a0, a1, _with_style(tag_text,
                                                  "overflow:hidden;zoom:1;")))
    return _apply_edits(html, edits)


def _with_style(tag_text, add):
    """Return an opening tag with `add` appended to its style="" (or a new
    style="" attribute created)."""
    if re.search(r"\bstyle\s*=\s*[\"']", tag_text, re.I):
        return re.sub(
            r"(\bstyle\s*=\s*[\"'])([^\"']*)",
            lambda mm: mm.group(1) + mm.group(2) + add,
            tag_text,
            count=1,
            flags=re.I,
        )
    return tag_text[:-1] + ' style="%s">' % add
