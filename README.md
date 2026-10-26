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
The tunnel is terminated with a self-signed certificate generated at startup
(via the `openssl` CLI), so browsers show a certificate warning once per
site; after you accept it, the request is downgraded to plain HTTP upstream.
Without `openssl`, `http://` sites still work but `https://` ones fail.

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
- `position: fixed`/`sticky` → `absolute` plus a small JS scroll handler injected before `</body>` that keeps the element in place while the page scrolls (IE6 ignores CSS `expression()` in the standards mode the proxy forces via the doctype). The element's top/left offset is preserved. (Offsets given via `bottom`/`right` are not preserved.)
- **Modern effects are approximated with IE6's DXImageTransform filters** rather than just dropped:
  - `linear-gradient()` / `radial-gradient()` → `DXImageTransform.Microsoft.Gradient` (end colors + nearest cardinal direction; `radial-gradient(... at <pos>)` points away from `<pos>`), with the first stop kept as a solid `background-color` fallback. Multi-layer backgrounds (gradient over `url(...)`) keep the image as `background-image`, with the gradient painted over it as the filter.
  - Elements with `background(-image)` + `background-size: cover|contain` get the image replaced by a nested `<img style="width:100%">` (IE6 can't scale background images, and such divs usually have no height of their own); the element's own `background-image` is suppressed inline.
  - `rgba()` alpha is preserved for the filters (as 8-digit `#AARRGGBB`); in plain CSS properties the 4-value `rgb()` form is left for IE6 to ignore.
  - `box-shadow` / `text-shadow` → `DXImageTransform.Microsoft.DropShadow` (first shadow only; `inset` dropped).
  - All filter colors are emitted as 8-digit `#AARRGGBB` — the DXImageTransform filters silently fail on 6-digit `#RRGGBB`.
  - `opacity: 0–1` → `alpha(opacity=0–100)`.
  - Existing IE `progid:` filters are passed through untouched.
- `rgba()`/`hsl()` → `rgb()`.
- `calc()` evaluated when possible; `vw`/`vh` approximated as px (assuming a 1024×768 screen) in the CSS, and elements sized in `vw`/`vh` additionally get a JS resize handler so they track the real window size (padding subtracted to approximate `box-sizing: border-box`).
- Unsupported properties (`border-radius`, `transform`, `transition`, `animation`, ...) and selectors (`:not()`, `:nth-*`, `:is()`, `:where()`, `:has()`, ...) removed.
- `::pseudo` → `:pseudo`; all `url()` references rewritten through the proxy.

### Images
- `image/webp` is converted to PNG on the fly **if Pillow is installed** (`pip install pillow`); otherwise it passes through unchanged.

### HTTPS downgrade
All upstream fetches use plain HTTP: a `/https/host/...` link is fetched from `http://host/...`. (Links in rewritten pages still carry their original scheme, so mixed content behaves as it would on the open web.)

### Ad blocking
Requests whose host matches a known ad/tracking domain (doubleclick, googlesyndication, google-analytics, googletagmanager, adnxs, taboola, outbrain, criteo, pubmatic, rubiconproject, ...) are served locally without any upstream request: an empty `204` for scripts/XHR, a transparent 1×1 GIF for image URLs. The list lives in `AD_DOMAINS` in `ie6proxy.py`.

## Caveats
- Rewriting is regex-based (no full parser): it handles the vast majority of real-world markup but exotic/nested constructs may slip through.
- Gradient/shadow approximations are lossy: only the two end colors and the nearest of four directions survive; multiple shadows collapse to the first. `border-radius`, `transform`, transitions and animations have no IE6 equivalent and are dropped.
- JavaScript that *runs* is left as-is; modern ES2015+ scripts will simply fail silently in IE6, which is why module scripts are dropped up front.
- POST requests and uploads are not proxied (IE6-era browsing was mostly GET).
