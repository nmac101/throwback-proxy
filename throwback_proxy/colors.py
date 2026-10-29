"""CSS colours and gradients: parsing, hex/ARGB conversion, DXImageTransform
gradient filters and hairline-border decls."""

import re

from .media import _balanced_paren


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
