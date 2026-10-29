"""CSS rewriting: down-grade modern CSS to what IE6 understands
(flexbox -> inline-block, position:fixed -> absolute, gradients/opacity -> DXImageTransform filters, rgba()/hsl() -> rgb(),
calc()/vw/vh/rem approximated, unsupported properties and selectors
dropped, url() references rewritten through the proxy)."""

import colorsys
import re
from urllib.parse import urljoin

from .config import VIEWPORT_HEIGHT, VIEWPORT_WIDTH
from .selectors import rewrite_selector, split_selectors
from .metrics import record_box_rules
from .media import (_balanced_brace, _balanced_paren, _feature_true,
    _length_px, _media_query_true, _resolve_media, _strip_at_blocks)
from .colors import (_COLOR_RE, _NAMED_COLORS, _gradient_angle,
    _gradient_filter, _gradient_stop_positions, _hairline_decls,
    _parse_color, _px, _to_argb, _to_hex)
from .urls import to_proxy



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
      will-change | scroll-behavior |
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
    """Expand unsupported font:inherit; preserve ordinary font shorthand."""
    v = value.strip()
    if v == "inherit":
        return ["font-family: inherit", "font-size: inherit",
                "font-weight: inherit", "font-style: inherit",
                "line-height: inherit"]
    # Font shorthand itself is supported (reference §4.3). Splitting it on
    # whitespace corrupts quoted families, keyword sizes and system fonts,
    # and loses the shorthand's reset of omitted style/weight properties.
    return ["font: %s" % v]


def _transform_decls(body):
    """Down-grade one declaration block to IE6.

    Modern effects are approximated with DXImageTransform filters.
    Layout measurements run separately on load/resize, never in generated
    CSS expressions.
    """
    filters = []
    opacity = None
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
        if p == "font":
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
            match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(%?)\s*(!\s*important)?", v, re.I)
            if match:
                amount = float(match[1]) / (100 if match[2] else 1)
                important = bool(match[3])
                if opacity is None or important or not opacity[1]:
                    opacity = (min(1, max(0, amount)), important)
            continue
        if p in ("box-shadow", "text-shadow"):
            # DX shadows filter the entire element (including its text),
            # not just the CSS shadow layer. They distort text rendering and
            # cannot reproduce centered blurred glows. Omit the effect rather
            # than forcing hasLayout and changing link rendering on hover.
            # Do not emit filter:none: that would cancel gradients/opacity.
            continue
        if p in ("background", "background-image") and "/" in v:
            # `center / 16px url(..)`: the CSS3 position/size form invalidates
            # the whole shorthand in IE6.  Drop the size part (keep the image).
            v = re.sub(
                r"/\s*(?:cover|contain|auto|[\d.]+(?:px|em|%)?)"
                r"(?:\s+(?:auto|[\d.]+(?:px|em|%)?))?", "", v)
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
        if p in ("mask-image", "-webkit-mask-image"):
            # Icon masks (`background-color: <icon colour>` + mask-image: the
            # glyph shape).  IE6 has no masks; show the glyph itself as the
            # background and suppress the (now-solid) colour fill.
            um = re.match(r"url\([^)]*\)", v)
            if um and "data:" not in um.group(0):
                decls.append("background-image: %s !important" % um.group(0))
                decls.append("background-color: transparent !important")
                decls.append("background-repeat: no-repeat")
            continue
        if p.startswith("-webkit-mask"):
            continue
        if p in ("filter", "-ms-filter"):
            if "progid:" in v.lower() or re.fullmatch(r"none\s*(?:!\s*important)?", v, re.I):
                # Explicit resets must cancel filters from earlier rules.
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
            # A full-width inline-block (multi-column item idiom) is just a
            # block, and `inline` would ignore the width.
            v = "block" if re.search(
                r"(?<![\w-])width\s*:\s*100%", body, re.I) else "inline"
            decls.append("zoom: 1")  # HACK-INLINE-BLOCK: inline + hasLayout
        elif p == "position" and v.lower() in ("fixed", "sticky"):
            v = "absolute"
            fixed = True
        decls.append("%s: %s" % (prop.strip(), v))
    if opacity is not None:
        percent = round(opacity[0] * 100)
        if percent < 100:
            filters.append("alpha(opacity=%d)" % percent)
        elif not filters:
            # Even alpha(opacity=100) routes text through IE's filter
            # renderer and can produce dark fringes. Clear the previous
            # alpha filter explicitly (dropping the declaration would leave
            # opacity:0 from a loading state in effect).
            decls.append("filter: none" + (" !important" if opacity[1] else ""))
    # IE6 ignores max-width, and margin:auto only centres a block that has
    # an explicit width AND hasLayout.  Two consequences for the ubiquitous
    # "max-width + margin:auto" centring idiom (very common on <body> and on
    # fixed-width page columns): (1) max-width becomes a plain fixed width,
    # and (2) layout must be present. Width triggers it on blocks; zoom:1
    # also covers elements whose display was changed during degradation.
    auto_center = re.search(
        r"(?<![\w-])margin(?:-left|-right)?\s*:\s*[^;]*\bauto\b", body, re.I)
    mw = re.search(r"(?<![\w-])max-width\s*:\s*([^;]+)", body, re.I)
    has_width = re.search(r"(?<![\w-])width\s*:\s*", body, re.I)
    if auto_center and (mw or has_width):
        if mw and not has_width:
            decls = [d for d in decls
                     if not re.match(r"\s*max-width\s*:", d, re.I)]
            decls.append("width: %s" % mw.group(1).strip())
        if not re.search(r"(?<![\w-])zoom\s*:", body, re.I):
            decls.append("zoom: 1")
    # No extra work for fixed/sticky here: the element is absolute with its
    # static top/left kept, and the HTML step (_pin_fixed_elements) adds a
    # JS scroll handler, avoiding repeatedly evaluated CSS expressions.
    decls = [d for d in map(_degrade_inherit, decls) if d]
    # BUG-NEGATIVE-MARGIN-CLIP: IE6 clips content pulled outside its
    # container by a negative horizontal margin; position:relative cures it.
    if (re.search(r"(?<![\w-])margin(?:-left|-right)?\s*:[^;]*-\d", body,
                  re.I)
            and not re.search(r"(?<![\w-])position\s*:", body, re.I)):
        decls.append("position: relative")
    # Image-replacement: `text-indent:-9999px` hides the label of a button
    # or link, but IE6 ignores text-indent on <input>, leaving the text
    # painted over the background.  A zero font-size hides it there too.
    ti = re.search(r"(?<![\w-])text-indent\s*:\s*-\s*(\d+)", body, re.I)
    if ti and int(ti.group(1)) >= 900:
        decls = [d for d in decls
                 if not re.match(r"\s*font-size\s*:", d, re.I)]
        decls.append("font-size: 0")
    # IE6 doubles a float's margin at the containing edge. display:inline
    # cures that bug without changing its floated box geometry. Preserve
    # intentionally hidden elements.
    fm = re.search(r"(?<![\w-])float\s*:\s*(left|right)\b", body, re.I)
    if (fm and re.search(r"(?<![\w-])margin(?:-" + fm[1] + r")?\s*:", body, re.I)
            and not re.search(r"display\s*:\s*none\b", body, re.I)):
        decls.append("display: inline")
    # IE6 quirk: a later plain declaration overrides an earlier !important
    # one in the same rule, so de-duplicate per property, keeping the last
    # value but preferring the !important variant.
    seen = {}
    for d in decls:
        p, _, v = d.partition(":")
        key = p.strip().lower()
        imp = bool(re.search(r"!\s*important\s*$", v, re.I))
        old = seen.get(key)
        if old is None or imp or not old[1]:
            # Keep the winning declaration at its source position relative
            # to shorthands that reset it (font vs font-weight, for example).
            seen.pop(key, None)
            seen[key] = (d, imp)
    decls = [d for d, _ in seen.values()]
    if filters:
        # IE6 only honours the FIRST filter in a list -- keep the most
        # visually significant one (gradient > opacity).
        def rank(f):
            fl = f.lower()
            if "gradient" in fl:
                return 0
            return 1
        selected = min(filters, key=rank)
        important = " !important" if selected.startswith("alpha(") and opacity and opacity[1] else ""
        decls.append("filter: %s%s" % (selected, important))
        if "zoom" not in seen:
            decls.append("zoom: 1")  # filters require hasLayout (§4.4)
    return "; ".join(decls)


# IE6 doesn't understand `inherit` (outside of direction/visibility) and
# drops the declaration, so a heading reset like `.mw-heading h2 {font-size:
# inherit}` never happens and nested sizes compound (h2 inside a 150% wrapper
# renders at 150% x 150%).  Where an equivalent exists we use it; otherwise
# the declaration is dropped, as IE6 would anyway.
_INHERIT_EQUIV = {
    "font-size": "100%",       # exactly equivalent
    "font-weight": "normal",   # resets UA bold on headings (the usual intent)
    "font-style": "normal",
    "font-variant": "normal",
    "line-height": "normal",
}


def _degrade_inherit(decl):
    prop, _, val = decl.partition(":")
    if val.strip().lower() != "inherit":
        return decl
    eq = _INHERIT_EQUIV.get(prop.strip().lower())
    return "%s: %s" % (prop.strip(), eq) if eq else None


def _eval_calc(inner):
    """Approximate the body of a calc() for IE6, which has no calc().

    Same-unit arithmetic is folded to a single value.  Mixed units can't be
    expressed; `N% +/- <length>` degrades to `N%`, anything else to `auto`
    (an invalid value for most properties, so IE6 drops the declaration
    cleanly instead of choking on a stray `calc(`)."""
    s = inner.strip()
    units = set(re.findall(r"[a-zA-Z%]+", s))
    if len(units) <= 1:
        bare = re.sub(r"[a-zA-Z%]+", "", s)
        # no `**` / `//`: keeps the evaluation cheap (no huge powers)
        if (re.fullmatch(r"[\d\s.+\-*/()]+", bare)
                and "**" not in bare and "//" not in bare):
            try:
                return "%g%s" % (eval(bare, {"__builtins__": {}}, {}),
                                 next(iter(units), ""))
            except Exception:
                pass
    m = re.fullmatch(r"\(?\s*(\d*\.?\d+)%\s*[-+]\s*\d*\.?\d+[a-z]+\s*\)?", s, re.I)
    if m:
        return m.group(1) + "%"
    return "auto"


def _replace_light_dark(css):
    """light-dark(a, b) -> a (the page is rendered with the light scheme)."""
    while True:
        m = re.search(r"light-dark\(", css)
        if not m:
            return css
        j = _balanced_paren(css, m.end() - 1)
        inner, depth, cut = css[m.end():j], 0, len(css[m.end():j])
        for k, ch in enumerate(inner):
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                cut = k
                break
        css = css[:m.start()] + inner[:cut].strip() + css[j + 1:]


def _replace_calc(css):
    """Replace every calc(...) (nested parens included) via _eval_calc."""
    while True:
        m = re.search(r"calc\(", css)
        if not m:
            return css
        j = _balanced_paren(css, m.end() - 1)
        css = css[:m.start()] + _eval_calc(css[m.end():j]) + css[j + 1:]


def _rewrite_css_urls(css, base, abs_base=None):
    def sub(m):
        inner = m.group(2).strip().strip("'\''")
        if not inner or inner.lower().startswith(("data:", "#")):
            return m.group(0)
        absu = urljoin(base, inner)
        return 'url("%s")' % to_proxy(absu, abs_base)

    return re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", sub, css)


def _degrade_selector(sel, synth=None):
    """Emulate static unsupported selectors using matching classes.

    Dynamic ancestor states that cannot be emulated remain unsupported,
    rather than broadening the set of matching elements.
    """
    sel = re.sub(r":root\b", "html", sel)
    sel = rewrite_selector(sel, synth)
    # A chain with an unhandled dynamic ancestor must stay unsupported;
    # broadening a child selector to descendants changes which nodes match.
    # drops above can empty out a comma-list part (e.g. [type='button'])
    return ",".join(p for p in (s.strip() for s in sel.split(",")) if p)


# Vendor / state pseudos that IE6 can't evaluate.  Stripping them would apply
# the rule to the plain element (e.g. `input::-webkit-search-cancel-button
# {display:none}` would hide the whole input), so the selector is dropped.
_UNSUPPORTED_PSEUDO_RE = re.compile(
    r"::?-(?:webkit|moz|ms|o)-|::(?:placeholder|selection|marker|backdrop|"
    r"file-selector-button)|:(?:placeholder-shown|focus-visible|checked|"
    r"disabled|enabled|required|optional|target|empty|"
    r"first-of-type|last-of-type|only-of-type|lang\()", re.I)


def _filter_rules(css, synth=None, box_rules=None):
    def sub(m):
        sel, body = m.group(1), m.group(2)
        # comma-list parts IE6 can't evaluate are dropped individually
        sel = ",".join(p for p in split_selectors(sel)
                       if not _UNSUPPORTED_PSEUDO_RE.search(p)
                       and not BAD_SELECTOR_RE.search(p))
        if not sel.strip():
            return ""
        if box_rules is not None and synth is not None:
            record_box_rules(sel, body, synth, box_rules)
        sel = sel.replace("::", ":")  # IE6 uses single-colon pseudos
        sel = _degrade_selector(sel, synth)
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
                # A qualified root (e.g. :root.large) is conditional too.
                tgt = (root_vars if any(p.strip() in (":root", "html")
                                        for p in split_selectors(sel))
                       else other_vars)
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
    # Root preferences must not leak into the sheet-wide approximation.
    # Vector defines --font-size-medium separately for Small/Standard/Large;
    # pooling those definitions made every page use the last (Large) value.
    # Materialize conditional declarations under the original root selector,
    # so independently served stylesheets still follow the page's classes.
    root_scopes = {}
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        definitions = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", rule[2]))
        if not definitions:
            continue
        for selector in split_selectors(rule[1]):
            selector = selector.strip()
            if re.fullmatch(r"(?:html|:root)(?:[.#][\w-]+)+", selector):
                selector = re.sub(r"^:root", "html", selector)
                root_scopes.setdefault(selector, {}).update(
                    (k, v.strip()) for k, v in definitions.items())
    conditional_names = {name for values in root_scopes.values() for name in values}
    sheet_vars = dict(other_vars)
    for name in conditional_names:
        sheet_vars.pop(name, None)
    sheet_vars.update(root_vars)

    def sub(text, local, inherited=None):
        env = dict(sheet_vars)
        env.update(inherited or {})
        env.update(local)
        cache, cyclic = {}, set()
        budget = [4096]

        def variable(name, stack):
            if name in stack:
                cyclic.update(stack[stack.index(name):])
                return None
            if name not in env or len(stack) >= 64:
                return None
            if name not in cache:
                value = expand(env[name], stack + (name,))
                cache[name] = None if name in cyclic else value
            return cache[name]

        def expand(value, stack=(), depth=0):
            # Bound both recursion and output: doubling references can grow
            # exponentially even without a cycle. Invalid values use the
            # caller's var() fallback or discard the declaration.
            if depth >= 64:
                return None
            pieces, pos, size = [], 0, 0
            var_pattern = re.compile(r"var\(", re.I)
            while True:
                m = var_pattern.search(value, pos)
                if not m:
                    tail = value[pos:]
                    return "".join(pieces) + tail if size + len(tail) <= 65536 else None
                budget[0] -= 1
                if budget[0] < 0:
                    return None
                end = _balanced_paren(value, m.end() - 1)
                if value[end:end + 1] != ")":
                    return None
                name, comma, fallback = value[m.end():end].partition(",")
                replacement = variable(name.strip(), stack)
                if replacement is None and comma:
                    replacement = expand(fallback.strip(), stack, depth + 1)
                if replacement is None:
                    return None
                prefix = value[pos:m.start()]
                size += len(prefix) + len(replacement)
                if size > 65536:
                    return None
                pieces.extend((prefix, replacement))
                pos = end + 1

        out = []
        for declaration in text.split(";"):
            prop, colon, value = declaration.partition(":")
            if not colon or prop.strip().startswith("--"):
                continue
            resolved = expand(value)
            if resolved is not None:
                out.append(prop + ":" + resolved)
        return ";".join(out)

    def walk(m):
        sel, body = m.group(1), m.group(2)
        local = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body))
        local = {k: v.strip() for k, v in local.items()}
        out = [sel + "{" + sub(body, local) + "}"]
        # Only variable-dependent declarations need conditional variants;
        # copying the whole rule would boost unrelated properties' specificity.
        declarations = [d for d in body.split(";")
                        if "var(" in d and not d.strip().startswith("--")]
        if not declarations:
            return "\n".join(out)
        for scope, values in root_scopes.items():
            changed_props = set()
            for declaration in declarations:
                value = sub(declaration, local, values)
                if value and value != sub(declaration, local):
                    changed_props.add(declaration.partition(":")[0].strip().lower())
            if not changed_props:
                continue
            # Keep duplicate declarations and font shorthands in source
            # order. A literal font-size after var() must still win.
            font_changed = any(p == "font" or p.startswith("font-")
                               for p in changed_props)
            changed = []
            for declaration in body.split(";"):
                prop = declaration.partition(":")[0].strip().lower()
                if (prop in changed_props or
                        (font_changed and (prop == "font" or prop.startswith("font-")))):
                    value = sub(declaration, local, values)
                    if value:
                        changed.append(value)
            selectors = []
            for part in split_selectors(sel):
                part = part.strip()
                # html cannot descend from html. Intersect conditions on
                # the root itself; ordinary targets become descendants.
                root = re.match(r"^(?:html|:root)(?=[.#:\s>+~\[]|$)", part)
                selectors.append(scope + part[root.end():] if root
                                 else scope + " " + part)
            out.append(",".join(selectors) + "{" + ";".join(changed) + "}")
        return "\n".join(out)

    return re.sub(r"([^{}]+)\{([^{}]*)\}", walk, css)


def transform_css(css, base, abs_base=None, synth=None, box_rules=None):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = _resolve_media(css)
    css = _flatten_css(css)
    css = _resolve_vars(css)
    # Fully-transparent rgba() -> nothing: a 4-value rgb() in a shorthand
    # (e.g. `background: rgba(0,0,0,0) url(...)`) invalidates the whole
    # declaration in IE6 and would drop the image layer with it.
    css = re.sub(
        r"(?:rgba|rgb)\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)\s*", "", css,
        flags=re.I)
    css = _strip_at_blocks(
        css, ["font-face", "keyframes", "-webkit-keyframes", "-moz-keyframes"]
    )
    css = _strip_at_blocks(css, ["media", "supports"])
    # rgba -> rgb, keeping the alpha as a 4th value: IE6 ignores such
    # declarations in CSS properties (as before), but _parse_color can
    # recover the alpha for the DX filters.
    css = re.sub(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+%?)\s*\)",
        r"rgb(\1,\2,\3,\4)", css, flags=re.I,
    )
    css = re.sub(r"\s{2,}", " ", css)
    css = re.sub(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", r"rgb(\1,\2,\3)", css,
        flags=re.I,
    )

    def _hsl(m):
        h, s, l = (
            (float(m.group(1)) % 360) / 360.0,
            min(1, max(0, float(m.group(2)) / 100.0)),
            min(1, max(0, float(m.group(3)) / 100.0)),
        )
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        alpha = "," + m.group(4) if m.group(4) else ""
        return "rgb(%d,%d,%d%s)" % (round(r * 255), round(g * 255), round(b * 255), alpha)

    css = re.sub(
        r"hsla?\(\s*(-?\d*\.?\d+)\s*,\s*(\d*\.?\d+)%\s*,"
        r"\s*(\d*\.?\d+)%\s*(?:,\s*(\d*\.?\d+%?)\s*)?\)",
        _hsl, css, flags=re.I
    )
    css = _replace_light_dark(css)
    css = _replace_calc(css)
    # vw/vh against the assumed viewport (config.py)
    css = re.sub(
        r"(\d*\.?\d+)\s*vw",  # require >=1 digit so a lone '.' can't reach float()
        lambda m: "%dpx" % round(float(m.group(1)) * VIEWPORT_WIDTH / 100),
        css,
    )
    css = re.sub(
        r"(\d*\.?\d+)\s*vh",  # require >=1 digit so a lone '.' can't reach float()
        lambda m: "%dpx" % round(float(m.group(1)) * VIEWPORT_HEIGHT / 100),
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
    css = _filter_rules(css, synth, box_rules)
    return css


