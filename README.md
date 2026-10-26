# throwback-proxy

An HTTP proxy in pure Python (stdlib only) that rewrites modern web pages so
they can be rendered by **Internet Explorer 6**, mostly preserving the original
page structure — and blocks ads on the way.

## Run

```sh
python3 ie6proxy.py [port]     # default: 8080
```

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
reloads over plain HTTP -- no TLS, no self-signed-certificate prompts.
The `CONNECT` tunnel is still terminated with a self-signed certificate
generated at startup (via the `openssl` CLI) so the redirect itself can be
delivered; without `openssl`, `http://` sites work but `https://` ones fail.

## What it does

### HTML (text/html)
- Down-converts HTML5 elements, keeping structure: `section/article/nav/header/footer/main/aside/figure` → `div`, `time/bdi/mark/...` → `span`, `<wbr>` removed.
- `<canvas>`, `<video>`, `<audio>` replaced with placeholders; `<video>`/`<canvas>` with `width`+`height` become a sized grey box so the layout doesn't collapse.
- `<picture>` kept as a div with its fallback `<img>`; `<source>` (srcset) dropped.
- `<script type="module">` and Google ad scripts removed; `defer`/`async` stripped from remaining scripts.
- Doctype swapped for XHTML 1.0 Strict (IE6 standards mode); a UTF-8 charset meta is injected if missing.
- Every `src` / `href` / `poster` (and `url()` inside inline `style=`) is rewritten to route through the proxy; `<base href>` is honoured.

### CSS (text/css and inline styles)
- `@media`, `@supports`, `@font-face`, `@keyframes` blocks removed.
- `display: flex/grid` → `block`/`inline`; flexbox/grid properties dropped. A flex container with `justify-content: center` also gets `text-align: center` so inline content stays centered.
- **Row flex layouts (sidebars!)**: when serving a page, its `<style>` blocks and linked stylesheets are fetched; direct block-level children of row-direction flex containers are laid out with the classic pre-flex pattern — any child with a known fixed pixel width (e.g. a 225px sidebar) gets `float:left`, its siblings get `overflow:hidden` so they wrap around the float and take the remaining width. If no child has a known width, all children fall back to `display:inline-block;vertical-align:top`. Column flex (e.g. the sidebar's own contents) degrades to plain block.
- **Float clearfix**: any element that contains a floated child (matched by class/id from the page's CSS) but doesn't already clear it gets `overflow:hidden`. In IE6 a block with floated children and no BFC lets the *next* sibling flow around the floats — e.g. a navbar with a `float:left` logo and `float:right` menu makes the following header wrap up into the navbar's top-right corner as a stray box.
- `position: fixed`/`sticky` → `absolute` plus a small JS scroll handler injected before `</body>` that keeps the element in place while the page scrolls (IE6 ignores CSS `expression()` in the standards mode the proxy forces via the doctype). The element's top/left offset is preserved. (Offsets given via `bottom`/`right` are not preserved.)
- `margin-top: auto` inside a pinned full-height container (the flexbox "push to bottom" idiom) → `position:absolute` with the container's padding/width, so the block keeps its in-flow geometry (inset by the padding, same content width) anchored to the container's bottom.
- **Modern effects are approximated with IE6's DXImageTransform filters** rather than just dropped:
  - `linear-gradient()` / `radial-gradient()` → `DXImageTransform.Microsoft.Gradient` (end colors + nearest cardinal direction; `radial-gradient(... at <pos>)` points away from `<pos>`), with the first stop kept as a solid `background-color` fallback. Multi-layer backgrounds (gradient over `url(...)`) keep the image as `background-image`, with the gradient painted over it as the filter.
  - Elements with `background(-image)` + `background-size: cover|contain|100%` **or an explicit pixel size** (e.g. `190px 40px`) get the image replaced by a nested `<img>` scaled to that size (IE6 can't scale background images, and such divs usually have no height of their own); the element's own `background-image` is suppressed inline. The mapping works off the *transformed*, top-level CSS (so image urls hidden in `var()` are found, and a mobile `@media` logo doesn't clobber the desktop one) and matches ID selectors as well as classes.
  - `rgba()` alpha is preserved for the filters (as 8-digit `#AARRGGBB`); in plain CSS properties the 4-value `rgb()` form is left for IE6 to ignore.
  - `box-shadow` / `text-shadow` → `DXImageTransform.Microsoft.DropShadow` (first shadow only; `inset` dropped).
  - All filter colors are emitted as 8-digit `#AARRGGBB` — the DXImageTransform filters silently fail on 6-digit `#RRGGBB`.
  - `opacity: 0–1` → `alpha(opacity=0–100)`.
  - Existing IE `progid:` filters are passed through untouched.
  - Filters are dropped from `body`/`html` rules: IE6 ignores them there, and a filter on an ancestor breaks the filters of all descendants (which is why page gradients can die when the body has e.g. a text-shadow). A `body` gradient background is repainted on a pinned, full-viewport `z-index:-1` div injected after `<body>` (matching `background-attachment: fixed`).
- `rgba()`/`hsl()` → `rgb()`.
- `calc()` evaluated when possible; `vw`/`vh` approximated as px (assuming a 1024×768 screen) in the CSS, and elements sized in `vw`/`vh` additionally get a JS resize handler so they track the real window size (padding subtracted to approximate `box-sizing: border-box`).
- Inline `<style>` blocks get the same treatment as linked stylesheets (sites like example.com keep all their CSS inline).
- **CSS nesting is flattened** (nested rules and `&` parent references, as used by archlinux.org's navbar) and **custom properties are resolved**: `var(--name[, fallback])` is substituted from `--name` definitions in the sheet (custom properties inherit in CSS, so definitions on a component's root rule reach its nested rules), and the `--*` declarations are dropped.
- Fully-transparent `rgba(0,0,0,0)` / 4-value `rgb(0,0,0,0)` is dropped from shorthand values: a 4-value `rgb()` would invalidate the whole declaration in IE6 and take e.g. the `url()` layer of a `background` shorthand down with it.
- `max-width` + `margin: auto` (the common centring idiom, e.g. on `body`) → plain `width`: IE6 ignores `max-width`, and `margin: auto` only centres a block with an explicit width.
- Unsupported properties (`border-radius`, `transform`, `transition`, `animation`, ...) and selectors (`:not()`, `:nth-*`, `:is()`, `:where()`, `:has()`, ...) removed.
- `::pseudo` → `:pseudo`; all `url()` references rewritten through the proxy.

### Images
- `image/webp` is converted to PNG on the fly **if Pillow is installed** (`pip install pillow`); otherwise it passes through unchanged.
- `image/svg+xml` is converted to PNG on the fly **if ImageMagick is installed** (`magick`/`convert`); IE6 cannot render SVG, otherwise it passes through unchanged.

### HTTPS downgrade
`https://` requests are redirected (302) to their `http://` equivalent, so everything loads over plain HTTP. Upstream fetches always use HTTP: a `/https/host/...` link is fetched from `http://host/...`. (Links in rewritten pages still carry their original scheme, so mixed content behaves as it would on the open web.)

### Ad blocking
Requests whose host matches a known ad/tracking domain (doubleclick, googlesyndication, google-analytics, googletagmanager, adnxs, taboola, outbrain, criteo, pubmatic, rubiconproject, ...) are served locally without any upstream request: an empty `204` for scripts/XHR, a transparent 1×1 GIF for image URLs. The list lives in `AD_DOMAINS` in `ie6proxy.py`.

## Caveats
- Rewriting is regex-based (no full parser): it handles the vast majority of real-world markup but exotic/nested constructs may slip through.
- Gradient/shadow approximations are lossy: only the two end colors and the nearest of four directions survive; multiple shadows collapse to the first. `border-radius`, `transform`, transitions and animations have no IE6 equivalent and are dropped.
- JavaScript that *runs* is left as-is; modern ES2015+ scripts will simply fail silently in IE6, which is why module scripts are dropped up front.
- POST requests and uploads are not proxied (IE6-era browsing was mostly GET).
