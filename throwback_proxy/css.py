"""CSS rewriting: down-grade modern CSS to what IE6 understands
(flexbox -> inline-block, position:fixed -> absolute, gradients/shadows/
opacity -> DXImageTransform filters, rgba()/hsl() -> rgb(),
calc()/vw/vh/rem approximated, unsupported properties and selectors
dropped, url() references rewritten through the proxy)."""

import colorsys
import re
from urllib.parse import urljoin

from .urls import to_proxy

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


def _strip_simple_nots(sel):
    """Remove :not(<simple>) occurrences from a selector.  IE6 supports
    :not() with a single simple argument (tag, .class, #id, :focus, ...),
    e.g. MediaWiki's `.mw-jump-link:not(:focus)` screen-reader hide trick --
    dropping those rules leaves the hidden helper links visible.  Complex
    arguments are left in place so BAD_SELECTOR_RE still catches them."""
    out = sel
    while True:
        m = re.search(r":not\(", out)
        if not m:
            return out
        j = m.end() - 1
        k = _balanced_paren(out, j)
        arg = out[j + 1:k].strip()
        if re.fullmatch(r"[a-zA-Z][\w-]*|#[\w-]+|\.[\w-]+|:[a-z-]+", arg):
            out = out[: m.start()] + out[k + 1 :]
        else:
            return out


def _unwrap_plain_media(css):
    """Unwrap always-true @media blocks (e.g. `@media screen`, `@media all`).

    Some sites (MediaWiki Vector 2022 in particular) wrap their core styles in
    a plain `@media screen { ... }` block.  IE6 knows no @media, and the
    generic strip below would delete the whole block along with the layout.
    Unwrap blocks whose condition is just `screen`/`all` with no `and`.
    """
    pat = re.compile(r"@media\s+(?:screen|all)\s*\{", re.I)
    while True:
        m = pat.search(css)
        if not m:
            return css
        j = _balanced_brace(css, m.end() - 1)
        if j is None:
            return css
        css = css[: m.start()] + css[m.end() : j] + css[j + 1 :]


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
    angle = _gradient_angle(inner)
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


def _gradient_angle(inner):
    """Direction of a gradient's argument list, snapped to a cardinal
    (0=to top, 90=to right, 180=to bottom, 270=to left)."""
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
    return min(
        (0, 90, 180, 270),
        key=lambda a: min(abs(a - angle), 360 - abs(a - angle)),
    )


def _gradient_stop_positions(inner):
    """[(position, color)] for each color stop in a gradient's argument list.
    Position is (value, unit-or-None) of the token FOLLOWING the color
    (CSS syntax: `<color> <position>`), or None when implicit."""
    matches = list(re.finditer(_COLOR_RE, inner))
    stops = []
    for k, cm in enumerate(matches):
        end = matches[k + 1].start() if k + 1 < len(matches) else len(inner)
        pm = re.match(r"\s*([\d.]+)(px|%)?", inner[cm.end():end])
        stops.append(((pm.group(1), pm.group(2)) if pm else None, cm.group(0)))
    return stops


def _hairline_decls(value, body):
    """IE6 declarations for gradients that are really hairlines, so the whole
    element isn't painted with a full-surface gradient.

    Two shapes occur in real-world CSS (e.g. MediaWiki Vector tab bars):
      * `linear-gradient(to top, #77c1f6 0, #e8f2f8 1px, #fff 100%)` -- a
        solid color with a 1px line of another color at one edge; becomes a
        solid background-color + 1px border on that edge.
      * a gradient shrunk to a 1px line via `background-size:1px 100%`
        (or `100% 1px`) with no-repeat -- a tab separator; becomes a 1px
        border on the corresponding edge.
    Returns a list of declarations, or None when the gradient is a real
    full-surface one.
    """
    matches = list(re.finditer(r"(?:linear|radial)-gradient\(", value))
    if not matches:
        return None
    m = matches[-1]
    i = value.index("(", m.start())
    j = _balanced_paren(value, i)
    inner = value[i + 1 : j]
    angle = _gradient_angle(inner)
    stops = _gradient_stop_positions(inner)
    if len(stops) < 2:
        return None

    def is_zero(pos):
        return pos is None or float(pos[0]) == 0

    def px_of(pos):
        if pos is None or pos[1] not in (None, "px"):
            return None
        return float(pos[0])

    def opaque(color):
        p = _parse_color(color)
        return p if p and p[1] != "00" else None

    # Shape 1: 1px-wide/tall background-size -> border on that edge.
    sizes = None
    sm = re.search(r"(?<![\w-])background-size\s*:\s*([^;]+)", body, re.I)
    if sm:
        sizes = sm.group(1).split()
    else:
        zm = re.search(r"/\s*([^;/]+)\s*$", value.strip())
        if zm:  # shorthand: background: ... / 1px 100%
            sizes = zm.group(1).split()
    if sizes:
        w, h = sizes[0], (sizes[1] if len(sizes) > 1 else sizes[0])
        color = None
        for c in (stops[-1][1], stops[0][1]):
            color = opaque(c)
            if color:
                break
        if color:
            if w == "1px" and h != "1px":  # vertical line, right edge
                return ["border-right: 1px solid #%s" % color[0]]
            if h == "1px" and w != "1px":  # horizontal line
                edge = "bottom" if angle == 180 else "top"
                return ["border-%s: 1px solid #%s" % (edge, color[0])]

    # Shape 2: first stop at 0, second within 2px, last at 100% -> solid
    # color with a 1px line of the first color at the starting edge.
    last = stops[-1][0]
    last_at_end = last is None or (last[1] == "%" and float(last[0]) == 100)
    if not (is_zero(stops[0][0]) and last_at_end):
        return None
    second = px_of(stops[1][0])
    if second is None or second > 2:
        return None
    solid = opaque(stops[-1][1])
    line = opaque(stops[0][1]) or opaque(stops[1][1])
    if not solid:
        return None
    edge = {0: "bottom", 180: "top", 90: "left", 270: "right"}[angle]
    decls = ["background-color: #%s" % solid[0]]
    if line:
        decls.append("border-%s: 1px solid #%s" % (edge, line[0]))
    return decls


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


_LIST_STYLE_TYPES = (
    "none", "disc", "circle", "square", "decimal", "decimal-leading-zero",
    "lower-roman", "upper-roman", "lower-greek", "lower-latin", "upper-latin",
    "armenian", "georgian", "lower-alpha", "upper-alpha",
)


def _expand_list_style(value):
    """Expand a `list-style` shorthand (and `list-style-type: none`) into
    longhand declarations.

    IE6's list-style shorthand does not reset list-style-image, so a
    `list-style: none` rule fails to override a lower-specificity
    `ul { list-style-image: url(...) }` (MediaWiki Vector's custom bullet
    icon) and every list keeps its bullets.  Emitting list-style-image
    explicitly fixes the cascade."""
    v = value.strip()
    m = re.search(r"url\([^)]*\)", v)
    img = m.group(0) if m else "none"
    rest = (v[: m.start()] + v[m.end():]).strip() if m else v
    typ = "disc"  # CSS initial value
    for t in rest.split():
        if t in _LIST_STYLE_TYPES:
            typ = t
            break
    return ["list-style-type: %s" % typ, "list-style-image: %s" % img]


def _expand_font(value):
    """Expand a `font` shorthand into longhand declarations.

    IE6's font shorthand is broken -- notably `font: inherit` (used by
    MediaWiki's `.mw-heading h1..h6` rule) resets font-size to the UA
    default, so every h2 renders at 2em, bigger than the h1s.  Longhand
    `font-size: inherit` etc. behave correctly, so the shorthand never
    passes through to the browser."""
    v = value.strip()
    if v == "inherit":
        return ["font-family: inherit", "font-size: inherit",
                "font-weight: inherit", "font-style: inherit",
                "line-height: inherit"]
    style = weight = size = line = None
    family = []
    for t in v.split():
        if t in ("italic", "oblique"):
            style = t
        elif t in ("bold", "bolder", "lighter") or re.fullmatch(r"\d{3}", t):
            weight = t
        elif re.search(r"[\d.]", t) and re.search(r"[a-z%]", t, re.I):
            if "/" in t and line is None:
                size, line = t.split("/", 1)
            elif size is None:
                size = t
        else:
            family.append(t.rstrip(","))
    out = []
    if style:
        out.append("font-style: %s" % style)
    if weight:
        out.append("font-weight: %s" % weight)
    if size:
        out.append("font-size: %s" % size)
    if line:
        out.append("line-height: %s" % line)
    if family:
        out.append("font-family: %s" % ",".join(family))
    return out


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
        if p == "font":  # IE6's broken shorthand; expand to longhand
            decls.extend(_expand_font(v))
            continue
        if p == "list-style":  # IE6's shorthand doesn't reset list-style-image
            decls.extend(_expand_list_style(v))
            continue
        if p == "list-style-type" and v == "none":
            # same IE6 bug: the type longhand doesn't clear a bullet image
            decls.append("list-style-type: none")
            decls.append("list-style-image: none")
            continue
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
            hb = _hairline_decls(v, body)
            if hb:
                # Hairline gradient (1px edge line / separator): a solid
                # color + border beats a full-surface gradient filter, and
                # avoids a filter on this element breaking descendant
                # filters in IE6.
                decls.extend(hb)
            else:
                filters.append(g[0])
                um = re.search(r"url\([^)]*\)", v)
                if um:
                    # Multi-layer background (e.g. gradient over an image):
                    # keep the image layer; the gradient paints over it as a
                    # filter.  Without this the url() would be dropped and
                    # the element would show only the gradient.
                    decls.append("background-image: %s" % um.group(0))
                elif p == "background":  # keep a solid fallback (hex only)
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
        elif p == "display" and v.startswith("table"):
            # IE6 has no CSS table display values.  A <caption> with
            # display:table-caption falls back to the UA default and renders
            # inline at the top-left of the page (MediaWiki infoboxes); a
            # block caption keeps it above its table with its border/bg.
            table_map = {"table": "block", "table-caption": "block",
                         "table-cell": "inline",
                         "table-row": "block", "table-row-group": "block",
                         "table-column": "inline",
                         "table-column-group": "inline",
                         "table-header-group": "block",
                         "table-footer-group": "block"}
            v = table_map.get(v, "block")
        elif p == "display" and v == "inline-block":
            # IE6 has no inline-block; inline is the closest degradation
            # (this is what makes font-size:0 + inline-block nav menus lay
            # out horizontally instead of stacking as block list-items).
            v = "inline"
        elif p == "position" and v.lower() in ("fixed", "sticky"):
            v = "absolute"
            fixed = True
        decls.append("%s: %s" % (prop.strip(), v))
    # IE6 ignores max-width, and margin:auto only centres a block that has
    # an explicit width -- so the ubiquitous "max-width + margin:auto"
    # centring idiom (very common on <body>) becomes a plain fixed width.
    mw = re.search(r"(?<![\w-])max-width\s*:\s*([^;]+)", body, re.I)
    if mw and not re.search(r"(?<![\w-])width\s*:\s*", body, re.I) \
            and re.search(
                r"(?<![\w-])margin(?:-left|-right)?\s*:\s*[^;]*\bauto\b",
                body, re.I):
        decls = [d for d in decls
                 if not re.match(r"\s*max-width\s*:", d, re.I)]
        decls.append("width: %s" % mw.group(1).strip())
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
        if BAD_SELECTOR_RE.search(_strip_simple_nots(sel)):
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


def _flatten_css(css):
    """Flatten CSS nesting (nested rules, `&` parent references) into flat
    rules.  IE6 knows no nesting; a nested rule's declarations are simply
    invisible to it.  Statement at-rules (@import) and block at-rules
    (@media, ...) are passed through verbatim."""
    out = []

    def find_close(s, i):
        """Index of the close brace matching the open brace at s[i]."""
        depth = 0
        while i < len(s):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return len(s) - 1

    def resolve(sel, parent):
        """Resolve a (possibly `&`-referencing) selector against its parent.
        Returns None for `&` rules with no parent (unresolvable)."""
        if "&" in sel:
            return sel.replace("&", parent) if parent else None
        return (parent + " " + sel) if parent else sel

    def emit_rule(sel, body):
        # split the body into top-level declarations and nested rules
        top, nested, j, m, decl_start = [], [], 0, len(body), 0
        while j < m:
            if body[j] == "{":
                nsel = body[decl_start:j].strip().rstrip(";").strip()
                nclose = find_close(body, j)
                if nsel:
                    rsel = resolve(nsel, sel)
                    if rsel:
                        nested.append((rsel, body[j + 1:nclose]))
                j = nclose + 1
                decl_start = j
            elif body[j] == ";":
                d = body[decl_start:j].strip()
                if d:
                    top.append(d)
                j += 1
                decl_start = j
            else:
                j += 1
        tail = body[decl_start:].strip()
        if tail:
            top.append(tail)
        if top:
            out.append(sel + " { " + "; ".join(top) + " }")
        for nsel, nbody in nested:  # parent first (cascade order)
            emit_rule(nsel, nbody)

    def parse_rules(text):
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if c in " \t\r\n;":
                i += 1
                continue
            if c == "}":
                i += 1
                continue
            brace = text.find("{", i)
            if brace == -1:
                break
            semi = text.find(";", i)
            if semi != -1 and semi < brace:
                # statement at-rule (e.g. @import "x.css";)
                out.append(text[i:semi + 1])
                i = semi + 1
                continue
            sel = text[i:brace].strip()
            close = find_close(text, brace)
            body = text[brace + 1:close]
            i = close + 1
            if not sel:
                continue
            if sel.startswith("@"):
                out.append(sel + " {" + body + "}")
                continue
            emit_rule(sel, body)

    parse_rules(css)
    return "\n".join(out)


def _collect_vars(css):
    """Return (root_vars, other_vars): custom-property definitions from
    :root rules and from all other rules, respectively.  Walking the rules
    (through @media nesting) lets us tell the default theme apart from
    theme-specific overrides."""
    root_vars, other_vars = {}, {}

    def walk(i, n):
        while i < n:
            c = css[i]
            if c in " \t\r\n;":
                i += 1
                continue
            if c == "}":
                i += 1
                continue
            brace = css.find("{", i)
            if brace == -1:
                break
            semi = css.find(";", i)
            if semi != -1 and semi < brace:  # statement at-rule (@import)
                i = semi + 1
                continue
            sel = css[i:brace].strip()
            depth, j = 0, brace
            while j < n:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = css[brace + 1:j]
            if sel.startswith("@"):
                walk(brace + 1, j)  # recurse into @media etc.
            else:
                tgt = root_vars if ":root" in sel else other_vars
                for vm in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+)", body):
                    tgt[vm.group(1)] = vm.group(2).strip()
            i = j + 1

    walk(0, len(css))
    return root_vars, other_vars


def _resolve_vars(css):
    """Substitute var(--name[, fallback]) using --name definitions from
    :root and from the same rule, then drop the custom-property
    declarations.  Unresolvable vars drop their declaration's value part
    (IE6 would ignore the declaration anyway)."""
    # Custom properties inherit in CSS; the common pattern is defining them
    # on a component's root rule and using them in nested rules, so pool
    # every definition in the sheet.  :root (the default/light theme) wins
    # over theme-specific rules that usually follow it (e.g. a dark theme on
    # html.skin-theme-clientpref-night or under prefers-color-scheme:dark),
    # which would otherwise clobber the default values.
    root_vars, other_vars = _collect_vars(css)
    sheet_vars = dict(other_vars)
    sheet_vars.update(root_vars)

    def sub(text, local):
        env = dict(sheet_vars)
        env.update(local)

        def rep(m):
            name, fb = m.group(1), m.group(2)
            if name in env:
                return env[name]
            return fb.strip() if fb else ""

        prev = None
        while prev != text:  # values may reference other vars
            prev = text
            text = re.sub(
                r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)", rep, text)
        return re.sub(r"(^|;)\s*--[\w-]+\s*:\s*[^;]+",
                      lambda m: m.group(1), text)

    def walk(m):
        sel, body = m.group(1), m.group(2)
        local = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body))
        local = {k: v.strip() for k, v in local.items()}
        return sel + "{" + sub(body, local) + "}"

    return re.sub(r"([^{}]+)\{([^{}]*)\}", walk, css)


def transform_css(css, base, abs_base=None):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = _flatten_css(css)
    css = _resolve_vars(css)
    css = _unwrap_plain_media(css)
    # Fully-transparent rgba() -> nothing: a 4-value rgb() in a shorthand
    # (e.g. `background: rgba(0,0,0,0) url(...)`) invalidates the whole
    # declaration in IE6 and would drop the image layer with it.
    css = re.sub(
        r"(?:rgba|rgb)\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)\s*", "", css, re.I)
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
    css = re.sub(r"\s{2,}", " ", css)
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
        r"(\d*\.?\d+)\s*vw",  # require >=1 digit so a lone '.' can't reach float()
        lambda m: "%dpx" % round(float(m.group(1)) * 10.24),
        css,
    )
    css = re.sub(
        r"(\d*\.?\d+)\s*vh",  # require >=1 digit so a lone '.' can't reach float()
        lambda m: "%dpx" % round(float(m.group(1)) * 7.68),
        css,
    )
    # rem: IE6 knows no root-relative units; 1rem = 16px (the root default).
    # Without this, e.g. MediaWiki's `.vector-body{font-size:0.875rem}` is
    # dropped and every em-based size in the content column balloons.
    css = re.sub(
        r"(\d*\.?\d+)rem",  # require >=1 digit so a lone '.' can't reach float()
        lambda m: "%gpx" % (float(m.group(1)) * 16),
        css,
    )
    css = _rewrite_css_urls(css, base, abs_base)
    css = _filter_rules(css)
    return css


