# Throwback Proxy
### Backporting the modern internet to old browsers
---
> [!WARNING]
> i wanna be as transparent as possible about this, so i'm gonna start off by saying that a qwen 27b model was heavily used for this project. this is my first ever attempt at "vibecoding" (while reviewing the code to some extent) since i dont have the time nor patience to research 25 year old webdev.
> 
> **no water was wasted on the making of this project, since i ran the model locally.** if that's not enough for you, code yourself an alternative, and i don't mean that in a bad way. i'd love to see more projects like this on foss spaces.
---
An HTTP proxy in pure Python (stdlib only) that rewrites modern web pages so
they can be rendered by **Internet Explorer 6**, mostly preserving the original
page structure — and blocks ads on the way.

## Run

```sh
python3 -m throwback_proxy [port]     # default: 8080
```

## Layout

| Module | Purpose |
| --- | --- |
| `throwback_proxy/config.py` | Global constants (host, port, user agent, ad domains, doctype) |
| `throwback_proxy/urls.py` | The proxy URL scheme (`to_proxy` / `from_proxy`) and ad / tracking domain matching |
| `throwback_proxy/css.py` | CSS rewriting (filters, units, selectors, var()/nesting) |
| `throwback_proxy/media.py` | `@media` evaluation and balanced-bracket helpers |
| `throwback_proxy/colors.py` | Colour parsing, gradient filters, hairline borders |
| `throwback_proxy/html.py` | HTML rewriting (tag down-conversion, scripts, URLs) |
| `throwback_proxy/selectors.py` | Static selector matching and synthetic IE6 classes |
| `throwback_proxy/markup.py` | HTML tag mapping and protection of raw text during rewrites |
| `throwback_proxy/metrics.py` | IE6 runtime measurements and viewport scrolling fixes |
| `throwback_proxy/compat.py` | Scoped component fallbacks, including MediaWiki TOCs and skip links |
| `throwback_proxy/flex.py` | Flex/grid row approximation and clearfix |
| `throwback_proxy/layout.py` | Layout fixes (fixed pinning, viewport sizing, backgrounds) |
| `throwback_proxy/fetch.py` | Upstream HTTP fetching |
| `throwback_proxy/images.py` | webp/svg → PNG conversion (optional deps) |
| `throwback_proxy/server.py` | The HTTP proxy server and entry point |

## Use it as your system / LAN proxy

This is the only way to use the proxy: there are no direct URLs like
`http://proxy:8080/http://example.com/` — point your browser at the machine
running the proxy and browse normally.

The proxy listens on `0.0.0.0`, so it is reachable from other machines on
your network too. Set the HTTP proxy in your browser/OS internet settings to
`127.0.0.1:8080` (or this machine's LAN IP for other devices), and add
`localhost;127.0.0.1` to the bypass/don't-proxy list:

- **IE6**: Tools → Internet Options → Connections → LAN settings →
  "Proxy server" → `127.0.0.1`, port `8080`.
- **Modern browsers / OS**: same address in the system proxy settings
  (e.g. Windows Settings → Network → Proxy, or Firefox → Settings → Network
  → Manual proxy).

In this mode the browser sends absolute-form requests for `http://` sites
(proxied directly) and `CONNECT host:443` tunnels for `https://` sites.
Any `https://` request (absolute-form or over a `CONNECT` tunnel) is
answered with a **302 redirect to the `http://` version**, so the browser
reloads over plain HTTP. Start browsing with an `http://` URL in IE6.
The `CONNECT` tunnel is still terminated with a self-signed certificate
generated at startup (via the `openssl` CLI) so the redirect itself can be
delivered. This can produce a certificate warning, and modern Python/OpenSSL
TLS defaults may reject IE6 before any redirect is delivered. Without
`openssl`, direct HTTPS entry fails; HTTP browsing still works.

## What it does

### HTML (text/html)
- Down-converts HTML5 elements, keeping structure: `section/article/nav/header/footer/main/aside/figure` → `div`, `time/bdi/mark/...` → `span`, `<wbr>` removed.
- `<canvas>`, `<video>`, `<audio>` replaced with placeholders; `<video>`/`<canvas>` with `width`+`height` become a sized grey box so the layout doesn't collapse.
- `<picture>` kept as a div with its fallback `<img>`; `<source>` (srcset) dropped.
- `<script type="module">` and Google ad scripts removed; `defer`/`async` stripped from remaining scripts.
- Doctype swapped for XHTML 1.0 Strict (IE6 standards mode); a UTF-8 charset meta is injected if missing.
- Every `src` / `href` / `poster` (and `url()` inside inline `style=`) is rewritten to route through the proxy; `<base href>` is honoured.

### CSS (text/css and inline styles)
- MediaWiki pages receive a compact, left-aligned Contents box with the following content cleared below it. Skip links stay keyboard-focusable but appear only when focused; this replaces the unsupported `:not(:focus)` hiding rule without removing navigation shortcuts. The TOC fallback uses ordinary floats and clearing, with no sizing script.
- `@media` queries are evaluated against the configured desktop viewport; matching rules are retained. `@supports not (...)` fallbacks are retained; other support blocks, font-face rules and keyframes are removed. Stylesheet scanning follows document order and the first `<base href>`, honours media/disabled/alternate attributes, and skips failed or non-CSS responses.
- `display: flex/grid` → `block`/`inline`; flexbox/grid properties dropped. A flex container with `justify-content: center` also gets `text-align: center` so inline content stays centered.
- **Row flex layouts (sidebars!)**: when serving a page, its `<style>` blocks and linked stylesheets are fetched; direct block-level children of row-direction flex containers are laid out with the classic pre-flex pattern — any child with a known fixed pixel width (e.g. a 225px sidebar) gets `float:left`, its siblings get `overflow:hidden;zoom:1` so they wrap around the float and take the remaining width. If no child has a known width, all children fall back to `display:inline-block;vertical-align:top`. Column flex (e.g. the sidebar's own contents) degrades to plain block.
- **Float clearfix**: elements containing floated children (matched by class/id from the page's CSS) receive containment fixes including `zoom:1` to trigger IE6's `hasLayout`. In IE6 a block with floated children and no layout lets the *next* sibling flow around the floats — e.g. a navbar with a `float:left` logo and `float:right` menu makes the following header wrap up into the navbar's top-right corner as a stray box.
- Floats with a margin on their floated side receive `display:inline` to prevent IE6 from doubling that margin, as seen in Vector's navigation tabs.
- Static selectors declaring `box-sizing` or icon-button sizing are matched to the page and handled by a bounded sizing pass after load and on debounced viewport resize. The proxy generates no CSS sizing expressions. The pass subtracts padding/borders, preserves icon buttons' font-relative dimensions, and ignores resize events caused by its own style writes. More specific `content-box` resets are respected, and viewport-unit sizing retains ownership of its dimensions. Hover/focus changes and newly inserted elements remain outside this fallback.
- Viewport scrolling is normalized after stylesheets load: when `html` has visible overflow, body overflow settings move to `html` and the body becomes overflow-visible. This avoids a separate body scrollbar on full-height pages such as Wii-Linux's wiki. Explicit root scrolling/clipping and quirks-mode pages are preserved.
- `position: fixed`/`sticky` → `absolute` plus a small JS scroll handler injected before `</body>` that keeps the element in place while the page scrolls (event handlers avoid the repeated evaluation cost of CSS expressions). The element's top/left offset is preserved. (Offsets given via `bottom`/`right` are not preserved.)
- `margin-top: auto` inside a pinned full-height container (the flexbox "push to bottom" idiom) → `position:absolute` with the container's padding/width, so the block keeps its in-flow geometry (inset by the padding, same content width) anchored to the container's bottom.
- **Modern effects are approximated with IE6's DXImageTransform filters** rather than just dropped:
  - `linear-gradient()` / `radial-gradient()` → `DXImageTransform.Microsoft.Gradient` (end colors + nearest cardinal direction; `radial-gradient(... at <pos>)` points away from `<pos>`), with the first stop kept as a solid `background-color` fallback. Multi-layer backgrounds (gradient over `url(...)`) keep the image as `background-image`, with the gradient painted over it as the filter.
  - Elements with `background(-image)` + `background-size: cover|contain|100%` **or an explicit pixel size** (e.g. `190px 40px`) get the image replaced by a nested `<img>` scaled to that size (IE6 can't scale background images, and such divs usually have no height of their own); the element's own `background-image` is suppressed inline. The mapping works off the *transformed*, top-level CSS (so image urls hidden in `var()` are found, and a mobile `@media` logo doesn't clobber the desktop one) and matches ID selectors as well as classes.
  - `rgba()` alpha is preserved for the filters (as 8-digit `#AARRGGBB`); in plain CSS properties the 4-value `rgb()` form is left for IE6 to ignore.
  - `box-shadow` / `text-shadow` are omitted: IE shadow filters distort text and cannot faithfully reproduce CSS shadows or centered glows. Hover colors and other supported styling are preserved.
  - All filter colors are emitted as 8-digit `#AARRGGBB` — the DXImageTransform filters silently fail on 6-digit `#RRGGBB`.
  - Partial opacity → `alpha(opacity=0–99)`. Fully opaque states explicitly clear the alpha filter instead of retaining `alpha(opacity=100)`, which can degrade text rendering. Generated filters also receive `zoom:1`, since filters require `hasLayout`.
  - Existing IE `progid:` filters are passed through untouched.
  - Filters are dropped from `body`/`html` rules: IE6 ignores them there, and a filter on an ancestor breaks the filters of all descendants (which is why page gradients can die when the body has e.g. a text-shadow). A `body` gradient background is repainted on a pinned, full-viewport `z-index:-1` div injected after `<body>` (matching `background-attachment: fixed`).
- `list-style` shorthand (and `list-style-type: none`) expanded to longhand with an explicit `list-style-image: none`: IE6's shorthand doesn't reset the image, so e.g. MediaWiki Vector's global `ul { list-style-image: url(bullet.svg) }` would otherwise keep bullets on the sidebar/footer/tab lists.
- `hsl()` is converted to complete `rgb()` values; alpha is retained for gradient filters. Ordinary CSS alpha colors remain a lossy fallback. Native font shorthand is preserved, including quoted families and system fonts. Inline-block is approximated with `display:inline;zoom:1` (or block for full-width elements).
- `calc()` evaluated when possible; `vw`/`vh` approximated as px (assuming a 1024×768 screen) in the CSS, and elements sized in `vw`/`vh` additionally get a JS resize handler so they track the real window size (padding subtracted to approximate `box-sizing: border-box`).
- Inline `<style>` blocks get the same treatment as linked stylesheets (sites like example.com keep all their CSS inline).
- **CSS nesting is flattened** (nested rules and `&` parent references, as used by archlinux.org's navbar) and **custom properties are resolved**: `var(--name[, fallback])` is substituted from `--name` definitions in the sheet (custom properties inherit in CSS, so definitions on a component's root rule reach its nested rules), and the `--*` declarations are dropped. Cycles and oversized expansions are bounded; unresolved values use fallbacks or discard their declaration.
- Custom properties under simple root class/ID conditions (such as `html.large` or `:root.dark`) produce conditional CSS declarations. This keeps ArchWiki's Small text preference at 14px instead of applying the Large preference's 20px value to every page. More complex custom-property scopes remain approximate.
- Fully-transparent `rgba(0,0,0,0)` / 4-value `rgb(0,0,0,0)` is dropped from shorthand values: a 4-value `rgb()` would invalidate the whole declaration in IE6 and take e.g. the `url()` layer of a `background` shorthand down with it.
- `max-width` + `margin: auto` (the common centring idiom, e.g. on `body`) → plain `width`: IE6 ignores `max-width`, and `margin: auto` only centres a block with an explicit width.
- Unsupported properties (`border-radius`, `transform`, transitions and animations) and selectors (`:not()`, `:nth-*`, `:is()`, `:where()`, `:has()`) are removed. Child/sibling combinators, first/last/only-child, attribute selectors, chained classes and ID/class combinations use synthetic classes matched against the original HTML. Selectors for converted HTML5 tags also use synthetic classes so their styles survive tag conversion. Unsupported dynamic states are not broadened to all elements. Selector-list entries are filtered individually.
- Duplicate declarations in one rule are de-duplicated, keeping the last value but preferring `!important`: IE6 lets a later plain declaration override an earlier `!important` one in the same rule.
- `::pseudo` → `:pseudo`; all `url()` references rewritten through the proxy.

### Images
- Enables IE6's background-image cache before stylesheets load, preventing repeated downloads of CSS icons during repaint. Successful image responses preserve origin caching restrictions, use a short private freshness lifetime when no cache policy is supplied, and provide validators for the actual delivered bytes. Converted PNGs have stable metadata-free output, and matching image validators receive `304 Not Modified`.
- `image/webp` is converted to PNG on the fly **if Pillow is installed** (`pip install pillow`); otherwise it passes through unchanged.
- `image/svg+xml` is converted to PNG on the fly **if ImageMagick is installed** (`magick`/`convert`); IE6 cannot render SVG, otherwise it passes through unchanged.

### HTTPS downgrade
`https://` links are rewritten to `http://` for the browser. The proxy follows a scheme-only HTTP → HTTPS redirect internally, using modern TLS upstream while serving HTTP to IE6; other redirects are relayed to the browser. Direct HTTPS entry through CONNECT has the TLS/certificate limitations described above.

### Ad blocking
Requests whose host matches a known ad/tracking domain (doubleclick, googlesyndication, google-analytics, googletagmanager, adnxs, taboola, outbrain, criteo, pubmatic, rubiconproject, ...) are served locally without any upstream request: an empty `204` for scripts/XHR, a transparent 1×1 GIF for image URLs. The list lives in `AD_DOMAINS` in `throwback_proxy/config.py`.

## Caveats
- HTMLParser handles stylesheet discovery and selector matching; other rewriting passes still use regexes. Script, style, textarea and title text is protected during structural rewrites. This is not a full HTML5 parser or browser CSS cascade: malformed HTML, selector specificity, scoped custom properties, CSS imports and complex layouts remain approximate.
- Gradient approximations are lossy: only the two end colors and the nearest of four directions survive. Shadows are omitted to avoid IE filter artifacts. `border-radius`, `transform`, transitions and animations have no IE6 equivalent and are dropped.
- Scripts are not fully transpiled. Inline `const`/`let` receive a best-effort conversion while literal/comment text is preserved; arrow functions and other modern syntax remain unsupported. Module scripts are dropped. JavaScript-dependent applications may not render usable content.
- POST requests and uploads are not proxied; form submissions requiring POST do not work. Rejected POST connections are closed so unread request bodies cannot be interpreted as another request. Cookies and authentication headers are not relayed upstream.
- Fixed-position emulation remains limited: elements with existing IDs and inline-only fixed styles are not currently pinned, and bottom/right anchoring is not preserved.
- Automated tests validate rewriting and localhost HTTP behaviour. Actual rendering, float/filter quirks and performance still need validation in an IE6 VM; passing tests do not establish visual parity.

## Tests

```sh
python3 -m unittest discover -s tests
```

Compatibility decisions are based on [ie6reference.md](docs/ie6reference.md), especially standards mode, `hasLayout`, selector support and filter requirements. The audit regressions live in `tests/test_audit.py`.

- **Newspaper columns**: simple class rules using `column-count` receive an IE6 table fallback. Direct child blocks stay intact and retain reading order; columns balance by item count rather than rendered height. Scoped selectors and width-only column sizing remain unsupported.
