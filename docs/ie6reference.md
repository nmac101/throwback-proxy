# Internet Explorer 6 (IE6) Rendering Reference for AI Agents: HTML, CSS, Behavior, Bugs and Hacks

IE6 is a CSS1-complete but CSS2.1-incomplete browser. It has no child, adjacent, attribute or `:first-child` selectors, no `:hover` outside links, no `min-`/`max-` sizing, no `position:fixed` and no native PNG alpha. Its rendering depends on two hidden switches: the DOCTYPE (quirks vs standards mode) and the proprietary `hasLayout` flag. If you predict or fix IE6 rendering, check those two switches first; most of the famous bugs are fixed by putting IE6 in standards mode and giving the right element "layout" (usually `zoom:1` or `height:1%` in an IE-only stylesheet).

## TL;DR
- **What works.**\[1\] Microsoft's MSDN page "CSS Compatibility and Internet Explorer" calls IE6 "the first fully CSS, Level 1-compliant version of Internet Explorer". What works:
  - CSS1-era selectors: type, class, ID, descendant, `:link`/`:visited`, `:first-line`/`:first-letter`.
  - Most box, font, text, color and background properties.
  - Proprietary extras: `filter`, `zoom`, `behavior`, `expression()`, `scrollbar-*`.
- **What doesn't.** Treat these as unsupported in IE6:
  - Selectors: `>`, `+`, `[attr]`, `:first-child`, `:focus`, `:before`/`:after`, chained classes `.a.b`, `:hover` on non-links.
  - Properties: `min-*`/`max-*`, `position:fixed`, `content`/counters, `outline`, `border-spacing`, `inherit`, alpha PNG.
- **What drives rendering.** IE6 is in quirks mode, with the IE5.5 border-box model, when there is no DOCTYPE, a partial DOCTYPE, or anything (including the `<?xml ?>` prolog) before the DOCTYPE. Most float and positioning bugs (peekaboo, guillotine, 3px jog, escaping floats, disappearing backgrounds) come from an element lacking `hasLayout`. Fix them with conditional-comment stylesheets that give layout (`zoom:1`) rather than with parser hacks.

## How to use this document (agent conventions)
- `status` values: `supported` | `partial` | `buggy` | `unsupported` | `proprietary`.
- `mode` values: `quirks` | `standards` | `both`.
- `confidence`: `high` means Microsoft documentation or several independent sources agree. `medium` means a well-known community finding where I couldn't retrieve the primary (positioniseverything.net) page text.
- Scope:
  - "IE6" means IE 6.0 on Windows 98/ME/2000/XP, including IE6 on XP SP2.
  - The SP2-specific difference that matters here is the memory-leak mitigation (see §5.6).
  - In IE6, quirks mode is effectively "frozen IE 5.5" (Henri Sivonen, "Activating Browser Modes with Doctype").
- IE7 fixed most bugs listed here **only in standards mode**. In his IEBlog post "Details on our CSS changes for IE7" (22 Aug 2006), Markus Mielke wrote that "we made over 200 behavior changes (bug fixes or new features) under strict mode… All this work (with the exception of transparent PNGs) has been done under the <!DOCTYPE> switch only", and that "To preserve application compatibility we will not make any behavioral changes to 'quirks mode' as it has been established since IE6." So an IE7+/IE11 page running in quirks mode reproduces many IE6 quirks too.

---

## 1. Rendering model and expected behavior

### 1.1 DOCTYPE switching (mode selection)
```yaml
doctype_switching:
  detection_api: document.compatMode   # "BackCompat" = quirks, "CSS1Compat" = standards
  rules:
    - input: "no DOCTYPE"
      mode: quirks
    - input: "HTML 4.01 Strict (with or without system URI)"
      mode: standards
    - input: "HTML 4.01 Transitional/Frameset WITH system URI (loose.dtd)"
      mode: standards        # IE6 has no separate 'almost standards' mode; its standards mode behaves like other browsers' almost-standards for table-cell image gaps
    - input: "HTML 4.01 Transitional WITHOUT system URI"
      mode: quirks
    - input: "XHTML 1.0 Strict/Transitional/Frameset, DOCTYPE is first thing in file"
      mode: standards
    - input: "<!DOCTYPE html> (HTML5)"
      mode: standards
    - input: "XML prolog <?xml version=...?> before any DOCTYPE"
      mode: quirks           # IE6 only; IE7 fixed ("<?xml> prolog no longer causes quirks mode")
    - input: "Comment, whitespace-carrying content, or any tag before DOCTYPE"
      mode: quirks
  agent_rule: "To get standards mode in IE6, the DOCTYPE must be the very first thing in the file. Never emit an XML declaration for text/html XHTML pages."
```
Sources: Markus Mielke (IE Program Manager), IEBlog, 22 Aug 2006, whose IE7 fix list includes "<?xml> prolog no longer causes quirks mode"; Sivonen ("the XML declaration makes IE 6 (but not IE 7!) trigger the Quirks mode"); 456 Berea Street.\[2\]

### 1.2 Quirks vs standards differences
| Behavior | Quirks mode (IE5.5 emulation) | Standards mode (IE6 "CSS1Compat") |
|---|---|---|
| Box model | `width`/`height` include padding and border (border-box) | W3C content-box |
| Root/canvas | `body` acts as root; scroll values on `document.body` | `html` is root; scroll values on `document.documentElement` |
| `width`/`height` on inline elements | Applied, and they trigger hasLayout | Ignored on non-replaced inline elements, and don't trigger hasLayout |
| `margin:0 auto` centering | Not honored; use `text-align:center` on parent | Honored for block boxes with width |
| Font-size inheritance into tables | Not inherited (legacy) | Inherited |
| Overflow / expanding boxes | Boxes grow to fit content | Boxes **still grow** in IE6 (fixed only in IE7 standards) |

### 1.3 XHTML handling
```yaml
xhtml:
  served_as_application/xhtml+xml:
    status: unsupported
    behavior: "IE6 does not render it as a document (it offers a download / file-handling dialog). Sivonen: 'IE 6, 7 and 8 did not support application/xhtml+xml.'"
    workaround: "Serve XHTML 1.0 as text/html following Appendix C, or content-negotiate on the Accept header."
  served_as_text/html:
    status: supported
    behavior: "Parsed with the ordinary tag-soup HTML parser; '/>' is tolerated. No XML well-formedness checks."
  xml_prolog: "Triggers quirks mode in IE6 (see 1.1)."
```

### 1.4 hasLayout (the core of IE6 layout)
```yaml
hasLayout:
  definition: "Proprietary internal flag. A layout element draws and sizes its own rectangular content box; elements without layout depend on the nearest ancestor with layout. Read with element.currentStyle.hasLayout. Read-only; cannot be set or unset directly."
  elements_with_layout_by_default: [html, body, table, tr, th, td, img, hr, input, button, select, textarea, fieldset, legend, iframe, embed, object, applet, marquee]
  triggers_IE6:
    - "width: any value other than auto (block elements; on inline elements only in quirks mode)"
    - "height: any value other than auto  (height:1% = 'Holly hack')"\[3\]
    - "float: left | right"
    - "position: absolute"
    - "display: inline-block"
    - "zoom: any value other than normal  (zoom:1 = side-effect-free trigger; invalid CSS, so put it in a conditional comment)"
    - "writing-mode: tb-rl"
    - "contentEditable=true (HTML attribute; do not use for this purpose)"
  NOT_triggers_in_IE6: ["position: relative", "overflow: hidden/auto (trigger only in IE7)", "min-/max-width/height (IE7 only)"]
  irreversible_cases: "display:inline-block followed by another display value in a LATER rule keeps layout (used as a validating trigger)."
  effects_of_having_layout:
    - "Contains (auto-clears) floated children."
    - "Next to a float, the whole box is displaced rectangularly instead of text wrapping around the float."\[3\]
    - "Margins do not collapse with children."
    - "Background/border painting is correct (cures peekaboo/guillotine/disappearing-background bugs)."
    - "An inline element with layout behaves like inline-block."
    - "Form controls inside a layout box inherit the summed horizontal margins of ancestors (form-margin bug)."
  removed_in: "IE8 standards mode"
  agent_rule: "When IE6 shows inexplicable vanishing, clipping, offset or non-containing behavior, first add zoom:1 (or height:1%) to the element and then to its container."
```
Sources: MS-CSS21 §5.1 (default layout elements); MSDN `hasLayout` property; hasLayout.net; Ingo Chao's "On having layout" (satzansatz.de).\[4\]

### 1.5 Conditional comments (IE5–IE9 only; the preferred way to target IE6)
```html
<!-- downlevel-hidden: only IE that matches sees it -->
<!--[if IE 6]><link rel="stylesheet" href="ie6.css"><ǃ[endif]-->
<!--[if lte IE 6]> ... <ǃ[endif]-->
<!--[if lt IE 7]> ... <ǃ[endif]-->

<!-- downlevel-revealed, valid form: non-IE browsers AND IE that matches see it -->
<!--[if !IE]><!--> <link rel="stylesheet" href="modern.css"> <!--<ǃ[endif]-->

<!-- Microsoft's original downlevel-revealed syntax (not valid HTML): <ǃ[if expression]> HTML <ǃ[endif]> -->
```
- Operators: `!`, `lt`, `lte`, `gt`, `gte`, `&`, `|`, `( )`. Feature token: `IE`, optionally followed by a version (`IE 6`).\[5\]
- Order: put the IE6 stylesheet **after** the main stylesheet so it overrides it.

### 1.6 JScript / DOM notes that affect rendering and scripting
```yaml
scripting_notes:
  events: "No addEventListener. Use attachEvent('onclick', fn)/detachEvent. Inside attachEvent handlers 'this' is window, not the element; use window.event and event.srcElement. No DOMContentLoaded."
  computed_style: "No getComputedStyle; use element.currentStyle (also exposes hasLayout). element.runtimeStyle overrides."
  ajax: "No native XMLHttpRequest (added in IE7); use new ActiveXObject('Msxml2.XMLHTTP') / ('Microsoft.XMLHTTP')."
  unknown_elements: "Elements IE6 doesn't know (abbr, HTML5 elements) can't be styled or nested correctly unless document.createElement('tagname') runs before parsing (basis of html5shiv)."\[6\]\[7\]
  opacity: "No CSS opacity; use filter:alpha(opacity=50). The element must have layout."
  scroll_metrics: "Standards mode: document.documentElement.scrollTop; quirks mode: document.body.scrollTop."
  css_expressions: "expression() re-evaluates on many events (mouse moves, resizes, scrolls) and can be CPU-heavy; self-disable by assigning runtimeStyle inside the expression."
```

---

## 2. HTML element support (HTML 4.01 / XHTML 1.0 + proprietary)

| Element | Status | Notes | Workaround |
|---|---|---|---|
| Structural/text (`div`, `span`, `p`, `h1`–`h6`, `ul/ol/li`, `dl/dt/dd`, `pre`, `blockquote`, `em`, `strong`, `code`, `cite`, `dfn`, `kbd`, `samp`, `var`, `sub`, `sup`, `del`, `ins`, `address`, `br`, `hr`) | supported | Watch list whitespace and float bugs (see §5) | — |
| `abbr` | unsupported (as an element) | Treated like an unknown tag: no CSS styling and poor DOM support. A W3C WAI list post said IE "treats `<abbr>` as it does `<somethingIjustMadeUp>`". `title` tooltip still shows\[8\]\[9\] | `document.createElement('abbr')` in `<head>`, or wrap the contents in `<span class>`, or use `acronym`\[6\]\[8\] |
| `acronym` | supported | Styleable; tooltip from `title` | — |
| `q` | partial | Recognized, but **no quotation marks are generated** (no `:before`/`:after`/`quotes`)\[10\] | Script-insert quotes, or `q{quotes:none}` for others plus literal quotes outside `q`\[10\]\[11\] |
| `button` | buggy | Submits **innerText instead of `value`**. IE6 also submits **every** `<button>` in the form, not just the clicked one.\[12\] Default `type` behaves as `button` in IE≤7\[13\]\[14\]\[15\] | Use `<input type="submit">` for multi-button forms; always set `type` explicitly\[13\]\[16\] |
| `label` | partial | `for`/`id` association works. Implicit association (wrapping the control without `for`) does not reliably focus the control (confidence: medium) | Always use `for`+`id` |
| `fieldset` / `legend` | partial | Both have layout by default. The fieldset background bleeds above the legend line, and legend positioning/margins are odd (confidence: medium) | Style the legend with `position:relative`/negative margins via IE-only CSS; avoid fieldset backgrounds |
| `select` | buggy | **Windowed control.** It draws above all page content and ignores `z-index`. IE's Chris Wilson explained in 1999 that "SELECT controls are windowed controls in our implementation, which in short means they sit above everything and don't play in z-order". Not fully CSS-styleable; no `title` tooltip or zoom. Microsoft's IEBlog post "For the SELECT few…" (17 Jan 2006) announced that "In IE7, the SELECT element has been changed into a windowless element." | Iframe shim under the overlay, or hide selects (`visibility:hidden`) while the overlay is open |
| `iframe` | supported | Windowless since IE5.5, respects `z-index`.\[17\] Used as the shim for `select`/ActiveX | `frameborder="0"`; `allowtransparency="true"` for a transparent background |
| `object` | buggy | Nested-object fallback content is poor (improved only in IE7). The `classid` attribute is limited to 128 characters; Mielke's 2006 IEBlog list of IE7 fixes includes "CLSID attribute of <object> tag no longer limited to 128 characters". ActiveX activation prompts | Use the `embed`-inside-`object` pattern, or script-based embedding (e.g., SWFObject); add `wmode="opaque"` for Flash under DHTML |
| `img` | supported | PNG alpha is not rendered (gray/opaque background). See §4 | AlphaImageLoader / DD_belatedPNG |
| `table`, `col`, `colgroup`, `caption`, `thead/tbody/tfoot` | partial | `border-spacing`, `caption-side` and `empty-cells` unsupported; `border-collapse` partial; `innerHTML` is read-only on table-section and row elements | Use `cellspacing` attribute; build rows with DOM `insertRow`/`insertCell` |
| `input`, `textarea` | supported (has layout) | Inherit summed ancestor horizontal margins when the container has layout | Use padding on containers instead of margins, or wrap inputs in a `span` |
| `frameset`/`frame`/`noframes` | supported | — | — |
| `marquee` | proprietary, supported | Scrolling text; has layout by default | Avoid |
| `bgsound`, `nobr`, `wbr`, `embed`, `noembed`, `xml` (data islands), `comment`, `ruby`/`rt`/`rp` | proprietary, supported | IE-only or legacy | Avoid in new content |
| `blink` | unsupported | Netscape-only | — |
| HTML5 elements (`section`, `nav`, `article`, `header`, ...) | unsupported | Parsed as empty unknown elements; children become siblings | `document.createElement` shim before the elements appear\[7\] |

Attribute notes:
- `title` tooltips work. On `img`, `alt` is also shown as a tooltip.
- `getAttribute('class')` returns null; use `'className'`. `for` is `htmlFor`.
- `getElementById` also matches elements whose `name` equals the id.
- `contenteditable` exists and triggers hasLayout.

---

## 3. CSS selector support

Primary source: Microsoft "CSS Compatibility and Internet Explorer" (MSDN cc351024), plus quirksmode.org and the Microsoft IE7 change list.

| Selector | Status | Notes / workaround |
|---|---|---|
| Type `E` | supported | — |
| ID `#id` | supported | `#id.class` combinations are parsed incorrectly (IE7 lists "Fixed parsing bugs for multi-class selectors and class selectors that are combined with id selectors")\[18\] |
| Class `.c` | partial | MSDN: "Prior to Internet Explorer 7, there was a two-class selector limit per element"\[1\] |
| Chained classes `.a.b` | buggy | quirksmode: "when confronted with p.test.small they act as if the selector is p.small" (only the last class counts).\[19\] Workaround: use a single combined class (`.a-b`) |
| Universal `*` | buggy | MSDN marks it "No" for IE6. In practice `*` matches but misbehaves: IE6 acts as if `html` has a parent, which is why `* html` matches (the star-html hack). Don't rely on `*` combinators\[20\] |
| Descendant `E F` | supported | Grandchild matching combined with other selectors was buggy (fixed in IE7)\[18\] |
| Child `E > F` | unsupported | The whole rule is ignored. Used as a hiding filter (`html>body .x`) |
| Adjacent `E + F` | unsupported | Use classes |
| General sibling `E ~ F` | unsupported | — |
| Attribute `[a]`, `[a=v]`, `[a~=v]`, `[a\|=v]` (and CSS3 `^= $= *=`) | unsupported | Add classes (e.g., `input.text`) |
| `:link`, `:visited` | supported | `:link` didn't match `<a href="#bookmark">` (fixed in IE7)\[18\] |
| `:hover` | partial | Only on `<a>` elements **with `href`**. Use csshover.htc, IE7.js or a Suckerfish-style `sfhover` class toggled by JS\[21\]\[22\]\[23\] |
| `a:hover descendant` | buggy | `a:hover span{}` may not apply unless `a:hover` itself changes some property (confidence: medium). Add e.g. `a:hover{background-position:0 0}` |
| `:active` | partial | MSDN lists "No". In practice it applies to `a` only and behaves like focus (stays "active" after click) |
| `:focus` | unsupported | Script `onfocus`/`onblur` to toggle a class |
| `:first-child` | unsupported | Add a `first` class |
| `:lang()` | unsupported | — |
| `:first-line`, `:first-letter` | partial | Supported, but IE6 needs a **space** before `{` (`p:first-line {`). Missing-first-letter and related bugs exist |
| `:before`, `:after` | unsupported | Insert markup or use `expression()`/script. The clearfix `:after` does nothing in IE6, so pair it with hasLayout\[10\]\[24\] |
| CSS3 pseudo-classes (`:nth-child`, `:last-child`, `:not`, `:target`, `:checked`, ...) | unsupported | — |
| `!important` | buggy | Ignored when the same property is redeclared later **in the same block** (see BUG-IMPORTANT) |

At-rules:
- `@import`: supported. `@media`: supported (types only, no media queries). `@charset`: supported.
- `@page`: partial.
- `@font-face`: supported but **EOT fonts only**.
- `@namespace`: unsupported.

---

## 4. CSS property support (IE6)

Status column = MSDN's IE6 value, refined with notes. "partial" in MSDN means the value set or behavior is incomplete.

### 4.1 Box, layout and positioning
| Property | Status | Notes | Workaround |
|---|---|---|---|
| `display` | partial | `block`, `inline`, `none`, `list-item` work. `inline-block` really works only on natively inline elements; on block elements it just triggers hasLayout. **No `table`, `table-cell`, `table-row`, `inline-table`, `run-in`** | Block→inline-block: `display:inline-block` in one rule, then `display:inline` in a later rule (or `display:inline; zoom:1`). Use real tables or floats for table layouts |
| `float` | buggy | Doubled margin, 3px jog, escaping floats, float drop | See §5 |
| `clear` | supported | Clearing elements inside layout boxes misbehave | Prefer containers with hasLayout |
| `position` | partial | `static`, `relative`, `absolute` work. **`fixed` is unsupported (treated as `static`)**\[25\]\[26\] | `position:absolute` + `expression()` tracking scroll (see HACK-FIXED) |
| `top`/`right`/`bottom`/`left` | partial | Absolute boxes with both `left` and `right` (or `top` and `bottom`) don't auto-size (fixed in IE7). `right`/`bottom` are 1px off in odd-sized containers | Set explicit width/height; use expressions or even dimensions |
| `z-index` | partial | Does not apply to windowed controls (`select`, ActiveX). Relatively positioned ancestors create stacking contexts even with `z-index:auto` | Iframe shim; give the positioned **ancestors** explicit, ordered z-indexes |
| `width`, `height` | supported (buggy) | `height` acts like `min-height` (boxes expand).\[23\] Ignored on inline elements in standards mode | — |
| `min-width`, `max-width`, `min-height`, `max-height` | unsupported | — | min-height hack; `expression()` for min/max-width |
| `margin`, `padding` | supported | `auto` horizontal margins work in standards mode only; doubled float margins; negative margins can clip content | `display:inline` on floats; `position:relative` on clipped children |
| `overflow` | partial | `visible` boxes **expand** instead of overflowing. `overflow:hidden` doesn't clip `position:relative` children unless the container is also positioned | Give the container `position:relative` |
| `overflow-x`, `overflow-y` | partial (proprietary in IE6) | Work | — |
| `clip` | supported | Requires the comma-less legacy syntax as well; `rect()` supported | — |
| `visibility` | supported | — | — |
| `box-sizing` | unsupported | IE6's quirks mode is effectively border-box | Use quirks mode deliberately (not recommended) or compute widths |

### 4.2 Color, background, borders
| Property | Status | Notes | Workaround |
|---|---|---|---|
| `color` | supported | Names, `#rgb`, `rgb()`, system colors. No `rgba`/`hsl` | — |
| `background`, `-color`, `-image`, `-repeat` | supported | PNG alpha not rendered; background-image flicker on hover (cache bug) | AlphaImageLoader/DD_belatedPNG; `document.execCommand('BackgroundImageCache', false, true)`\[27\] |
| `background-attachment` | partial | `fixed` works **only on `body`/root**; on other elements it behaves like `scroll` (IE7 made it work everywhere)\[18\] | Emulate with fixed body background |
| `background-position` | partial | Keyword/length/percentage mostly work; edge cases buggy | — |
| `transparent` keyword | partial | Works for `background-color`; **`border-color:transparent` renders as solid** (transparent borders added only in IE7) | Use `filter:chroma(color=pink)` with a matching border color |
| `border`, `border-*-width`, `border-*-color` | supported | — | — |
| `border-style` | partial | **1px `dotted` renders as `dashed`** (fixed in IE7).\[18\] Other styles OK | Use a background image for dotted 1px lines |
| `border-collapse` | partial | Collapsing works but conflict resolution is buggy | Test; prefer `cellspacing="0"` |
| `border-spacing`, `caption-side`, `empty-cells` | unsupported | — | `cellspacing` attribute; put `&nbsp;` in empty cells |
| `outline` (all) | unsupported | — | Border or background on `:hover`/focus class |
| `border-radius`, `box-shadow`, `opacity` | unsupported | — | Images, VML, or `filter:alpha(opacity=N)` / `filter:progid:DXImageTransform.Microsoft.Shadow` |

### 4.3 Text, fonts, lists, generated content
| Property | Status | Notes |
|---|---|---|
| `font`, `font-family`, `font-size`, `font-style`, `font-variant` | supported | Keyword `font-size` in quirks mode is one step larger (`small` = default) |
| `font-weight` | partial | Numeric weights collapse to normal/bold |
| `line-height`, `letter-spacing`, `text-align`, `text-decoration`, `text-indent`, `text-transform`, `vertical-align`, `direction`, `unicode-bidi` | supported | Line-height bug with inline replaced elements (see §5) |
| `word-spacing` | partial | — |
| `white-space` | partial | `normal`, `pre`, `nowrap` work. No `pre-wrap`/`pre-line`. Layout elements don't inherit `white-space` correctly |
| `list-style`, `-image`, `-position` | supported | — |
| `list-style-type` | partial/unsupported for CSS2.1 values | CSS1 values (`disc`, `circle`, `square`, `decimal`, `lower/upper-roman`, `lower/upper-alpha`) work. CSS2.1 additions (`decimal-leading-zero`, `lower-greek`, `armenian`, `georgian`, `lower-latin`) don't. Floated or layout `li` lose their markers |
| `content`, `counter-reset`, `counter-increment`, `quotes` | unsupported | — |
| `page-break-before`/`-after` | supported | `page-break-inside`, `orphans`, `widows` unsupported |
| `cursor` | supported | Use `hand` + `pointer` (IE5.x needs `hand`); `.cur`/`.ani` URLs supported |
| `inherit` keyword | unsupported | Repeat explicit values |
| `auto` keyword | partial | — |
| Units | supported | `px pt pc cm mm in em ex %`. No `rem`, `vw`, `vh`, `ch` |

### 4.4 Proprietary IE properties (IE6)
| Property | Purpose | Notes |
|---|---|---|
| `filter` | Visual filters: `alpha(opacity=N)`, `progid:DXImageTransform.Microsoft.AlphaImageLoader(src='x.png', sizingMethod='crop\|image\|scale')`, `Gradient`, `Shadow`, `DropShadow`, `chroma`, `BasicImage(rotation=N)`, `Matrix` | MSDN: "The object that the filter is applied to must have layout before the filter effect will display."\[28\] Filters can disable ClearType text and hurt performance |
| `zoom` | Scales the element | `zoom:1` is the canonical hasLayout trigger\[29\] |
| `behavior` | Attaches `.htc` HTML Components (`url(csshover.htc)`) | URL is relative to the **HTML document**, not the CSS file; server must send the `text/x-component` MIME type |
| `expression()` | JScript evaluated as a CSS value | Re-evaluated constantly; slow; removed in IE8 standards mode |
| `scrollbar-face-color`, `-arrow-color`, `-track-color`, `-shadow-color`, `-highlight-color`, `-3dlight-color`, `-darkshadow-color`, `-base-color` | Scrollbar colors | Apply to `body` in quirks mode and to `html` in standards mode |
| `writing-mode` | Vertical text (`tb-rl`) | Also a hasLayout trigger |
| `word-wrap: break-word`, `word-break` | Break long words | Useful against the expanding-box bug |
| `text-overflow: ellipsis` | Truncation | Partial; needs `overflow:hidden; white-space:nowrap` |
| `text-justify`, `text-autospace`, `layout-grid*`, `ruby-*`, `ime-mode`, `accelerator`, `text-underline-position` | Typography/IME | IE-only |

---

## 5. Known bugs and rendering quirks (catalog)

Format: `id`, `symptom`, `trigger`, `mode`, `workaround`, `fixed_in`, `confidence`. Markus Mielke's IEBlog post of 22 Aug 2006 lists IE7 (standards mode) as fixing "All bugs on positioniseverything.net except the 'escaping floats' bug (which is planned for the future)", and that covers the positioniseverything.net (PIE) bugs below.

### 5.1 Float bugs
```yaml
- id: BUG-DOUBLE-MARGIN
  name: Doubled float-margin bug
  symptom: "Horizontal margin on a floated box is doubled (margin-left:10px renders as 20px)."\[20\]\[23\]\[30\]
  trigger: "float:left with margin-left (or float:right with margin-right); the margin touches the container edge."
  mode: both
  workaround: "Add display:inline to the float (ignored by other browsers since floats are block-level)."
  example: |
    .sidebar { float:left; margin-left:10px; display:inline; }
  fixed_in: IE7
  confidence: high

- id: BUG-3PX-JOG
  name: Three-pixel text jog
  symptom: "Text (line boxes) beside a float is pushed 3px away from the float, then jumps back below the float's bottom; can also force float drop in tight fixed-width layouts."\[20\]\[31\]
  trigger: "Non-floated block with inline content adjacent to a float; the adjacent block lacks layout."
  mode: both
  workaround: "Give the adjacent block layout (height:1% / zoom:1) so the whole box shifts instead of the lines; and/or margin-right:-3px on the float in IE6-only CSS; budget 3px in fixed-width layouts."\[3\]\[32\]
  fixed_in: IE7
  confidence: high

- id: BUG-PEEKABOO
  name: Peekaboo bug
  symptom: "Content beside/after a float disappears on load and reappears on scroll, resize, hover or text selection."\[23\]\[33\]
  trigger: "Float inside a container with a background, followed by a clearing element touching the float; container lacks layout."
  mode: both
  workaround: "Give the container layout (zoom:1 / height:1%); or position:relative on the float and container; or line-height on the container."\[33\]\[34\]
  fixed_in: IE7
  confidence: high

- id: BUG-GUILLOTINE
  name: Guillotine bug
  symptom: "Bottom part of a float (or the container) is chopped off or restored when hovering links."\[33\]\[35\]
  trigger: "Container without layout + uncleared float + links in non-floated content after the float + a:hover rules that change background/padding etc."\[36\]
  mode: both
  workaround: "Clear the float with a real element inside the container (clear:both) or give the container layout."\[35\]\[37\]
  fixed_in: IE7
  confidence: high

- id: BUG-DUPLICATE-CHARS
  name: Duplicate characters (ghost text) bug
  symptom: "Last characters of the last float are duplicated below it."
  trigger: "Multiple floats in a row separated by HTML comments (or elements with display:none / hidden inputs); last float nearly fills container width."
  mode: both
  workaround: "Remove comments between floats; display:inline on the floats; margin-right:-3px on the last float; or wrap comments in conditional comments."
  fixed_in: IE7
  confidence: high

- id: BUG-ESCAPING-FLOATS
  name: Escaping floats bug
  symptom: "Nested floats extend beyond a container that should enclose them, or the container encloses them inconsistently."
  trigger: "Container with nested floats and no stated dimensions (no layout)."
  mode: both
  workaround: "Give the container layout (Holly hack: * html .c {height:1%})."\[38\]
  fixed_in: "NOT fixed in IE7 (Microsoft explicitly excluded it)"\[39\]
  confidence: high

- id: BUG-DUPLICATE-INDENT
  name: Duplicate indent / float text-indent bug
  symptom: "First line of text next to a float is indented by the float's margin amount."
  trigger: "Float with margin-left next to inline text."
  mode: both
  workaround: "display:inline on float (as for the doubled margin)."
  fixed_in: IE7
  confidence: medium

- id: BUG-FLOAT-DROP
  name: Float drop (via expanding box)
  symptom: "A floated column drops below its neighbor."\[35\]
  trigger: "Oversized content (wide image, long URL) inside a fixed-width float makes the box expand."
  mode: both
  workaround: "overflow:hidden on the float in IE-only CSS; word-wrap:break-word; constrain content."\[35\]
  confidence: high
```

### 5.2 Box, overflow and sizing bugs
```yaml
- id: BUG-EXPANDING-BOX
  name: Expanding box problem
  symptom: "Fixed width/height box grows to contain oversized content instead of overflowing."
  trigger: "Content wider or taller than the box with overflow:visible."
  mode: both
  workaround: "overflow:hidden (IE-only) or word-wrap:break-word; Microsoft: in IE7 'Overflow now works correctly! (That means boxes do not automatically grow any more.)'"
  confidence: high

- id: BUG-ITALIC-OVERFLOW
  name: IE italics bug
  symptom: "Italic text near a container's right edge widens the box: horizontal scrollbar or float drop."
  trigger: "font-style:italic (em, i, italic blockquote) in a constrained box."
  mode: both
  workaround: "Remove italic styling where it causes the problem; or overflow:hidden on the container (IE6-only CSS); give the container layout."
  confidence: high (symptom) / medium (overflow fix)

- id: BUG-BROKEN-BOX-MODEL
  name: Quirks-mode box model
  symptom: "Boxes narrower than in other browsers by padding+border."
  trigger: "Quirks mode (no/partial DOCTYPE, XML prolog)."
  mode: quirks
  workaround: "Use a standards DOCTYPE first in file; never add XML prolog."
  confidence: high

- id: BUG-EMPTY-ELEMENT-HEIGHT
  name: Minimum line height on empty/short boxes
  symptom: "A div with height:2px renders ~12-20px tall."
  trigger: "Small explicit height; IE reserves a line box at current font-size."
  mode: both
  workaround: "font-size:0; line-height:0; overflow:hidden on the element (or put an HTML comment inside)."
  confidence: medium

- id: BUG-MIN-HEIGHT
  name: No min-height / max-*
  symptom: "min-height, min-width, max-width, max-height ignored."
  mode: both
  workaround: "min-height hack (see HACK-MIN-HEIGHT); expression() for widths."
  confidence: high

- id: BUG-1PX-ODD
  name: Magic 1px offset (right/bottom positioning)
  symptom: "Absolutely positioned child with right:0 or bottom:0 sits 1px off."
  trigger: "Containing block has odd width/height."
  mode: both
  workaround: "Use even dimensions or adjust with IE6-only right:-1px/bottom:-1px."
  confidence: medium

- id: BUG-WHITESPACE-LIST
  name: List whitespace / gap bug
  symptom: "Vertical gaps appear between list items of a vertical nav menu."
  trigger: "li containing a display:block anchor, with whitespace/newlines between </li> and <li>; elements without layout."
  mode: both
  workaround: "Give the anchors layout (zoom:1 or width), or display:inline on li, or remove whitespace between li tags."
  fixed_in: IE7 ('List-item whitespace bug fixed')
  confidence: high

- id: BUG-LINE-HEIGHT-REPLACED
  name: Line-height bug (inline replaced elements)
  symptom: "Lines containing images or form controls get wrong line-height (half-leading dropped); text lines squish or uneven spacing."
  trigger: "Inline replaced element (img, input) in a line box with a set line-height."
  mode: both
  workaround: "Add vertical margins to the replaced element equal to the half-leading, or display:block the image / put it in its own line."
  fixed_in: IE7
  confidence: medium

- id: BUG-FORM-MARGIN-INHERIT
  name: Inherited margin bug on form controls
  symptom: "input/textarea are shifted by the sum of ancestors' horizontal margins."
  trigger: "Form control whose containing box has layout; ancestors have margin-left/right."
  mode: both
  workaround: "Use padding on ancestors, or wrap control in an inline span/label."
  confidence: high
```

### 5.3 Stacking and positioning bugs
```yaml
- id: BUG-SELECT-ZINDEX
  name: Select box shows through (windowed controls)
  symptom: "<select> (and ActiveX/Flash without wmode) draws over dropdown menus/modals regardless of z-index."
  trigger: "Any positioned overlay overlapping a select in IE6. MSDN zIndex: 'The property does not apply to windowed controls, such as select objects.'"
  mode: both
  workaround: |
    Iframe shim: an absolutely positioned ‹iframe src="javascript:false;" frameborder="0"> (filter:alpha(opacity=0))
    placed under the overlay, same size/position, lower z-index than the overlay; or hide selects while the overlay is open.
  fixed_in: IE7 (select became windowless)
  confidence: high

- id: BUG-ZINDEX-STACKING
  name: Relative-positioning stacking context bug
  symptom: "An absolutely positioned dropdown with high z-index appears under a later sibling block."
  trigger: "Each position:relative ancestor creates its own stacking context even with z-index:auto."
  mode: both
  workaround: "Set explicit z-index on the relatively positioned ancestors so the menu's ancestor is higher than the subsequent ones."
  confidence: high

- id: BUG-NEGATIVE-MARGIN-CLIP
  name: Negative margin clipping
  symptom: "Parts of an element pulled outside its parent with negative margins are invisible."
  trigger: "Element without layout pulled out of a parent with layout."
  mode: both
  workaround: "position:relative (and/or zoom:1) on the pulled element."
  confidence: medium

- id: BUG-DISAPPEARING-LIST-BG
  name: Disappearing list-background bug
  symptom: "Backgrounds on list items (or the list) vanish."
  trigger: "List inside a relatively positioned float (as recalled; primary page not retrievable)."
  mode: both
  workaround: "Remove position:relative from the float or apply position:relative / layout to the list items."
  fixed_in: IE7
  confidence: medium
```

### 5.4 Selector, cascade and parser bugs
```yaml
- id: BUG-IMPORTANT
  name: !important ignored within one block
  symptom: "In {margin:2% 0 !important; margin:20px 0;} IE6 uses the second value."
  trigger: "Same property repeated in the same declaration block after an !important declaration."
  mode: both
  workaround: "Avoid duplicate declarations; or exploit it as an IE6 filter (basis of the min-height hack)."
  fixed_in: IE7 ('Addressed !important issues')
  confidence: high

- id: BUG-MULTICLASS
  name: Chained class selector bug
  symptom: ".a.b matches every element with class b."
  mode: both
  workaround: "Use single unique classes."
  confidence: high

- id: BUG-HOVER-NON-ANCHOR
  name: :hover only on anchors
  symptom: "li:hover, div:hover dropdowns don't open."\[23\]\[40\]
  mode: both
  workaround: "csshover.htc (whatever:hover), IE7.js, or Suckerfish JS adding a class on mouseover."\[21\]\[22\]\[23\]
  confidence: high

- id: BUG-FIRST-LINE-SPACE
  name: :first-line / :first-letter need whitespace before brace
  symptom: "p:first-letter{...} ignored."
  workaround: "Write 'p:first-letter {'."
  fixed_in: IE7
  confidence: high
```

### 5.5 Image and PNG bugs
```yaml
- id: BUG-PNG-ALPHA
  name: No PNG alpha transparency
  symptom: "24/32-bit PNG alpha areas render gray/blue-ish opaque. (8-bit PNG with binary transparency works like GIF.)"
  mode: both
  workaround:
    - "AlphaImageLoader filter for img/background (element needs layout; background-repeat/position not supported; sizingMethod crop|image|scale)."\[23\]\[28\]\[41\]
    - "Links/inputs over an AlphaImageLoader background become unclickable -> give them position:relative (and z-index)."\[41\]\[42\]\[43\]
    - "DD_belatedPNG (VML-based) supports background-position/repeat and a:hover."\[27\]
    - "Use 8-bit PNG/GIF fallbacks served via conditional comments."
  fixed_in: IE7 (native alpha PNG)\[41\]
  confidence: high

- id: BUG-BG-FLICKER
  name: Background image flicker / re-request on hover
  symptom: "CSS background images flicker on hover (images re-downloaded when cache set to 'every visit')."
  workaround: "document.execCommand('BackgroundImageCache', false, true) in try/catch; or CSS sprites."\[27\]
  confidence: high
```

### 5.6 Memory and performance quirks
```yaml
- id: BUG-JSCRIPT-DOM-LEAK
  name: Circular reference memory leak
  symptom: "Memory grows with each page view in long sessions/AJAX apps."
  trigger: "Circular reference between a DOM node and a JScript object (typically an event-handler closure referencing its element). MS: the JScript GC 'manages the lifetime of JScript objects but not of DOM objects'."\[44\]\[45\]
  behavior: "Original IE6: leaked memory freed only when the IE process terminates. IE6 on XP SP2 after the June update: per the IEBlog post 'Tools for detecting memory leaks' (Nov 2007), IE7 releases 'all references to Jscript objects attached to the DOM tree when IE navigates away' and 'We've also made the same changes in IE6 on Windows XP SP2 (shipped originally with the June Update).' (Douglas Crockford cites fix 929874; KB number not independently confirmed.)"
  workaround: "Null out handlers/expandos on unload (detachEvent, el.onclick=null), avoid closures capturing DOM nodes, use event delegation; detect with Drip/sIEve."\[46\]\[47\]\[48\]
  confidence: high

- id: PERF-EXPRESSIONS
  name: CSS expression() cost
  symptom: "Sluggish scrolling/mouse movement."
  trigger: "expression() re-evaluated on every recalc."
  workaround: "One-shot expressions that set runtimeStyle and disable themselves; prefer JS on load/resize."\[27\]
  confidence: high

- id: PERF-FILTERS
  name: Filter cost
  symptom: "Slow rendering, lost ClearType on filtered text."
  trigger: "filter: alpha/AlphaImageLoader on many/large elements."
  workaround: "Limit filters to small elements; prefer 8-bit PNG."
  confidence: medium
```

---

## 6. Hacks and workarounds catalog

| id | Technique | Targets | Code | Notes / recommendation |
|---|---|---|---|---|
| HACK-CC | Conditional comments | IE5–9 by version | `<!--[if lte IE 6]><link rel="stylesheet" href="ie6.css"><ǃ[endif]-->` | **Preferred.** Valid, future-proof, lets you use `zoom`/`filter`/`expression` without polluting the main CSS |
| HACK-STAR-HTML | Star-html | IE≤6 (all modes); IE7 in quirks mode | `* html .box { height:1%; }` | IE6 treats `html` as having a parent. Fixed in IE7 standards\[20\] |
| HACK-UNDERSCORE | Underscore property | IE≤6 only | `.box { height:auto; _height:1%; }` | Invalid CSS; IE7 ignores it in standards mode\[3\] |
| HACK-STAR-PROP | Star property | IE≤7 | `.box { *zoom:1; }` | Invalid CSS |
| HACK-CHILD-FILTER | Child selector filter (hide from IE6) | All but IE≤6 | `html>body .box { min-height:200px; }` | Rules IE6 can't parse are skipped |
| HACK-IMPORTANT | !important filter | IE6 | `margin:0 !important; margin:5px;` | IE6 uses the last value |
| HACK-HOLLY | Holly hack | IE5–6/Win (hidden from IE5/Mac) | `/* \*/ * html .x { height:1%; } /* */` | Gives layout; IE stretches the 1% height anyway\[3\]\[38\] |
| HACK-ESCAPE | Backslash escapes | Distinguish IE5.x from IE6 | `back\ground-color: transparent;` | IE5.x ignores escaped property names that IE6 parses |
| HACK-MIN-HEIGHT | Min-height | IE6 | `.x { min-height:500px; height:auto !important; height:500px; }` | IE6 treats `height` like min-height and ignores the `!important` override\[23\]\[37\]\[49\] |
| HACK-MAX-WIDTH | Max-width expression | IE6 | `_width: expression(document.body.clientWidth > 960 ? "960px" : "auto");` | Use in IE-only CSS; costs CPU |
| HACK-FIXED | position:fixed emulation | IE6 | `* html #bar { position:absolute; top: expression((ignoreMe = document.documentElement.scrollTop ? document.documentElement.scrollTop : document.body.scrollTop) + 'px'); }` plus `* html, * html body { background-image:url(about:blank); background-attachment:fixed; }` | The second rule stops jitter while scrolling\[50\] |
| HACK-INLINE-BLOCK | inline-block on blocks | IE6–7 | `.x { display:inline-block; } .x { display:inline; }` (or `*display:inline; zoom:1`) | Relies on irreversible layout |
| HACK-CLEARFIX | Easy clearing | All + IE6 | `.cf:after{content:".";display:block;height:0;clear:both;visibility:hidden}` `* html .cf{height:1%}` | IE6 ignores `:after` and contains floats via hasLayout. `overflow:hidden` alone doesn't contain floats in IE6 without layout\[24\]\[51\] |
| HACK-IFRAME-SHIM | Cover selects | IE5.5–6 | `‹iframe class="shim" src="javascript:false;" frameborder="0" style="position:absolute;filter:alpha(opacity=0)">` under overlay | bgiframe jQuery plugin automates this\[52\] |
| HACK-PNG-FILTER | AlphaImageLoader | IE5.5–6 | `_background:none; _filter:progid:DXImageTransform.Microsoft.AlphaImageLoader(src='/img/a.png',sizingMethod='crop');`\[53\] | `src` path is relative to the **page**, not the CSS\[54\] |
| LIB-DD-BELATEDPNG | DD_belatedPNG (Drew Diller) | IE6 | `<!--[if IE 6]><script src="DD_belatedPNG.js"></script><script>DD_belatedPNG.fix('.png_bg, img');</script><ǃ[endif]-->`\[27\]\[55\] | VML-based; supports `background-position`/`-repeat` and `a:hover`\[27\]\[56\] |
| LIB-IEPNGFIX | iepngfix.htc / supersleight | IE5.5–6 | `img, .png { behavior:url(iepngfix.htc); }` | Filter-based; same clickability caveats\[22\]\[23\] |
| LIB-CSSHOVER | whatever:hover (Peter Nederlof's csshover.htc) | IE5–6 | `body { behavior:url(csshover.htc); }` | Adds `:hover`/`:active` (and `:focus` in v3) to any element\[21\] |
| LIB-IE7JS | IE7.js / IE8.js / IE9.js (Dean Edwards) | IE5–8 | `<!--[if lt IE 7]><script src="IE7.js"></script><ǃ[endif]-->` | Script emulation of child/adjacent/attribute selectors, `:hover`, min/max, PNG alpha, `position:fixed`. Has a load-time cost |
| LIB-HTML5SHIV | createElement shim | IE6–8 | `document.createElement('abbr'); document.createElement('section');` | Must run before the elements appear in the body\[7\]\[57\] |
| HACK-SUCKERFISH | Suckerfish dropdowns | IE6 | JS adds `sfhover` class on `onmouseover`; CSS `li:hover ul, li.sfhover ul {display:block}`\[23\] | Lightweight alternative to `.htc` |

---

## Recommendations (agent decision rules)
1. **Diagnose the mode first.** Read `document.compatMode`. If it's `BackCompat`, fix the DOCTYPE (first line, no XML prolog) before anything else. Many "IE6 bugs" are just quirks-mode box-model differences.
2. **Use conditional comments, not parser hacks.** Put all IE6 fixes in one `ie6.css` loaded via `<!--[if lte IE 6]>`. There you can safely use `zoom:1`, `filter`, `expression()`, `_`/`*` properties and `height:1%`.
3. **Default fix order for a layout glitch:**
   1. `zoom:1` on the element, then on its parent.
   2. `display:inline` on floats with side margins.
   3. `position:relative` on vanishing or clipped children.
   4. `overflow:hidden` for expanding boxes.
   5. Explicit `z-index` on positioned ancestors.
   6. An iframe shim for selects.
4. **Write selectors as single class/ID/type/descendant selectors only.** No `>`, `+`, `[attr]`, `:first-child`, `.a.b` or `:hover` on non-links. Add classes server-side.
5. **Plan for degradation.** Use 8-bit PNGs or DD_belatedPNG, give fixed heights through the min-height hack, and put `position:fixed` elements behind `expression()` emulation. Accept slight visual differences rather than heavy script polyfills on low-end machines.
6. **Scripting.** Branch on `attachEvent`, `currentStyle`, and `ActiveXObject` for XHR. Clean up handlers to avoid leaks, especially for users on pre-SP2 or unpatched IE6.

## Caveats
- **Primary-source gap.** The original positioniseverything.net bug pages are no longer online at that domain (it now hosts unrelated content), and I couldn't retrieve archived copies. Entries marked `confidence: medium` rely on secondary write-ups and recall. Verify them in a real IE6 VM before treating them as definitive.
- **MSDN vs practice.** Microsoft's compatibility table marks the universal selector and `:active` as "No" for IE6, but both partially work in practice.\[1\] I report them as `buggy`/`partial`.
- **IE6 point versions.** IE6 SP1 and IE6 on XP SP2 are rendering-identical for CSS. XP SP2 added the pop-up blocker, Information Bar and, after the June Update (per Microsoft's IEBlog), the memory-leak mitigation.
- **Emulation is not IE6.** IE7+ quirks mode, IE11 "IE5 quirks" document mode and IE Tester-style tools approximate IE6 but differ in places (e.g., `select` windowing, PNG alpha, standards-mode bugs). Sivonen notes the imitations of earlier versions "are not perfect".\[2\] Test in a genuine IE6 VM.

## Sources

1. [CSS Compatibility and Internet Explorer](<https://learn.microsoft.com/en-us/previous-versions/cc351024(v=vs.85)>)
2. [Activating Browser Modes with Doctype](https://hsivonen.fi/doctype/)
3. [On having layout\[转\] - chen eric - 博客园](https://www.cnblogs.com/lovko/archive/2009/11/11/1601401.html)
4. [\[MS-CSS21\]: The hasLayout HTML Elements](https://learn.microsoft.com/en-us/openspecs/ie_standards/ms-css21/94d6fe1a-87a6-4e27-b181-bba78bfef72c)
5. [What is HTML comment - HTML tutorials - w3resource](https://www.w3resource.com/html/HTML-comments.php)
6. [JavaScript, abbr and Internet Explorer — Jon Gibbins, Dotjay](https://dotjay.com/2008/02/javascript-abbr-internet-explorer)
7. [The WHATWG Blog — Supporting New Elements in IE](https://blog.whatwg.org/supporting-new-elements-in-ie)
8. [Styling \<abbr\> in IE](http://www.sovavsiti.cz/css/abbr.html)
9. [\- From: Jon Hanna \<jon@hackcraft.net\>](https://lists.w3.org/Archives/Public/w3c-wai-ig/2005OctDec/0179.html)
10. [Fixing Quotes in Internet Explorer - Juicy Studio](https://juicystudio.com/article/fixing-ie-quotes.php)
11. [Recommend against using the \<q\> element (or fix its behavior) · Issue #10216 · whatwg/html · GitHub](https://github.com/whatwg/html/issues/10216)
12. [Coping With Internet Explorer's Mishandling of Buttons](https://allinthehead.com/retro/330/coping-with-internet-explorers-mishandling-of-buttons/)
13. [IE6 and Multiple Button Submit Elements - Vance Lucas](https://vancelucas.com/blog/ie6-and-multiple-button-submit-elements/)
14. [lists.w3.org](https://lists.w3.org/Archives/Public/public-whatwg-archive/2008Oct/0424.html)
15. [\- From: Benjamin Hawkes-Lewis \<bhawkeslewis@googlemail.com\>](https://lists.w3.org/Archives/Public/w3c-wai-ig/2010AprJun/0013.html)
16. [Why does submit\_tag generate an input element instead of a button element? - Ruby on Rails Discussions](https://discuss.rubyonrails.org/t/why-does-submit-tag-generate-an-input-element-instead-of-a-button-element/79150)
17. [zIndex property (Windows)](<https://learn.microsoft.com/en-us/previous-versions/windows/internet-explorer/ie-developer/platform-apis/aa704176(v=vs.85)>)
18. [Details on our CSS changes for IE7](https://learn.microsoft.com/en-us/archive/blogs/ie/details-on-our-css-changes-for-ie7)
19. [CSS selectors - desktop](https://www.quirksmode.org/css/selectors/)
20. [www.YoYoBrain.com - Tools to Memorize and Learn](https://www.slideshare.net/slideshow/advancedcssqs/6761744)
21. [Internet Explorer (IE6) CSS Hover - CSS Snipplr Social Repository](https://snipplr.com/view/1912/internet-explorer-ie6-css-hover)
22. [habr.com](https://habr.com/en/articles/13067)
23. [CSS Hacks of the IE6 Era: A Nostalgic Field Guide](https://modern-css.com/articles/ie6-css-hacks/)
24. [@charset "UTF-8";](https://research.spec.org/fileadmin/yaml/core/iehacks.css)
25. [w3.org](https://w3.org/Style/Examples/007/menus.en.html)
26. [What happens in Quirks Mode in web browsers?](https://jkorpela.fi/quirks-mode.html)
27. [GitHub - effy-yin/DD\_belatedPNG: DD\_belatedPNG解决IE6下的PNG透明问题 · GitHub](https://github.com/effy-yin/DD_belatedPNG)
28. [AlphaImageLoader Filter](<https://learn.microsoft.com/en-us/previous-versions/windows/internet-explorer/ie-developer/platform-apis/ms532969(v=vs.85)?redirectedfrom=MSDN>)
29. [In depth analysis of IE browser's proprietary CSS attribute hasLayout - ITworkman](https://www.itworkman.com/in-depth-analysis-of-ie-browsers-proprietary-css-attribute-haslayout/)
30. [Appendix I IE Hacks](https://www.webstepbook.com/supplements-2ed/chapters/wpsbs2ed-appendixi-ie-hacks.pdf)
31. [Floating bugs](https://www.456bereastreet.com/archive/200309/floating_bugs/)
32. [style iefixes](https://spdf.gsfc.nasa.gov/pub/software/old/selected_software_from_nssdc/root/css/style_iefixes.css)
33. [IE 6 Float Bugs - Tutorial](https://www.vskills.in/certification/tutorial/ie-6-float-bugs-2/)
34. [imf.org](https://imf.org/external/07/css/hacks.css)
35. [15 Common CSS bugs and their easy fixes](https://www.sanwebe.com/2015/03/15-common-css-bugs-and-their-easy-fixes)
36. [IE6 bug: hop on hover -- seen this one? - CSS forum at WebmasterWorld - WebmasterWorld](https://www.webmasterworld.com/css/4482.htm)
37. [Hacks Archives](https://artofdeveloping.com/category/hacks/)
38. [How To Attack An IE/Win Bug, describing the Holly hack and the "relative" fix.](https://arantius.com/misc/mirror/hollyhack.html)
39. [Figure 25. The Microsoft Box Model demonstrated in IE6 with quirks mode](https://www.oreilly.com/library/view/releasing-css/059652935X/chapter-10.html)
40. [Csshover.htc ? - CSS - W3Schools Forum](https://w3schools.invisionzone.com/topic/29423-csshoverhtc/)
41. [24 ways: Transparent PNGs in Internet Explorer 6](https://perishablepress.com/wp/wp-content/online/pages/png-supersleight.html)
42. [10 Weird HTML Hacks That Shaped The Internet](https://tedium.co/2023/11/24/weird-html-hacks-history/)
43. [IE6 PNG Transparency Fix with Javascript](https://blog.creonfx.com/internet-explorer/ie6-png-transparency-fix-javascript/)
44. [JScript Memory Leaks - Douglas Crockford](https://www.crockford.com/javascript/memory/leak.html)
45. [Circular Memory Leak Mitigation](<https://learn.microsoft.com/en-us/previous-versions/windows/internet-explorer/ie-developer/platform-apis/dd361842(v=vs.85)>)
46. [Internet Explorer Event Handler Leaks · Reign Drops Fall…](https://www.reigndropsfall.net/2011/01/05/internet-explorer-event-handler-leaks/)
47. [tools for detecting memory leaks](https://learn.microsoft.com/en-us/archive/blogs/ie/tools-for-detecting-memory-leaks)
48. [text stringlengths 92 14.7k | conversation\_id int64 207 134k | embedding list | cluster int64 13 13 |](https://huggingface.co/datasets/AdapterOcean/code_instructions_standardized_cluster_13/viewer/default/train?p=15)
49. [CSS Min-Height Hack in Internet Explorer - Blog](https://mainelydesign.com/blog/view/css-min-height-hack-internet-explorer-ie)
50. [Make position:fixed work in IE6 · GitHub](https://gist.github.com/158243)
51. [0% found this document useful (0 votes)](https://www.scribd.com/document/61582216/CSS-Conditional-Cheat-Sheet)
52. [web design - z-index and iframe \[SOLVED\]](https://www.daniweb.com/digital-media/ui-ux-design/threads/325112/z-index-and-iframe)
53. [PNG Hack/Fix for IE 6](https://css-tricks.com/snippets/css/png-hack-for-ie-6/)
54. [Relative path in filter: progid:DXImageTransform.Microsoft.AlphaImageLoader not working \[#185866\]](https://www.drupal.org/project/lightbox2/issues/185866)
55. [vdata.nikkei.com](https://vdata.nikkei.com/prj1/averageincome/js/libs.js)
56. [0% found this document useful (0 votes)](https://www.scribd.com/document/837443779/png)
57. [Styling the ABBR tag in IE6 — The Tall Designer](http://www.thetalldesigner.com/blog/2011/02/21/styling-the-abbr-tag-in-ie6)
