# throwback-proxy

An HTTP proxy in pure Python (stdlib only) that rewrites modern web pages so
they can be rendered by **Internet Explorer 6**, mostly preserving the original
page structure — and blocks ads on the way.

## Run

```sh
python3 ie6proxy.py [port]     # default: 8080
```

Then browse to `http://127.0.0.1:8080/` (landing page with a URL box), or
directly to e.g. `http://127.0.0.1:8080/p/http/example.com/`.

Proxy URL scheme: `/p/<scheme>/<host[:port]>/<path>?<query>`

## What it does

### HTML (text/html)
- Down-converts HTML5 elements, keeping structure: `section/article/nav/header/footer/main/aside/figure` → `div`, `time/bdi/mark/...` → `span`, `<wbr>` removed.
- `<canvas>`, `<video>`, `<audio>` replaced with empty inline placeholders.
- `<picture>` kept as a div with its fallback `<img>`; `<source>` (srcset) dropped.
- `<script type="module">` and Google ad scripts removed; `defer`/`async` stripped from remaining scripts.
- Doctype swapped for XHTML 1.0 Strict (IE6 standards mode); a UTF-8 charset meta is injected if missing.
- Every `src` / `href` / `poster` (and `url()` inside inline `style=`) is rewritten to route through the proxy; `<base href>` is honoured.

### CSS (text/css and inline styles)
- `@media`, `@supports`, `@font-face`, `@keyframes` blocks removed.
- `display: flex/grid` → `block`/`inline`; all flexbox/grid properties dropped.
- `position: fixed` → `absolute`.
- Gradients collapsed to their first color; `rgba()`/`hsl()` → `rgb()`.
- `calc()` evaluated when possible; `vw`/`vh` approximated as px (assuming a 1024×768 screen).
- Unsupported properties (`border-radius`, `box-shadow`, `transform`, `transition`, `animation`, `filter`, ...) and selectors (`:not()`, `:nth-*`, `:is()`, `:where()`, `:has()`, ...) removed.
- `::pseudo` → `:pseudo`; all `url()` references rewritten through the proxy.

### Images
- `image/webp` is converted to PNG on the fly **if Pillow is installed** (`pip install pillow`); otherwise it passes through unchanged.

### Ad blocking
Requests whose host matches a known ad/tracking domain (doubleclick, googlesyndication, google-analytics, googletagmanager, adnxs, taboola, outbrain, criteo, pubmatic, rubiconproject, ...) are served locally without any upstream request: an empty `204` for scripts/XHR, a transparent 1×1 GIF for image URLs. The list lives in `AD_DOMAINS` in `ie6proxy.py`.

## Caveats
- Rewriting is regex-based (no full parser): it handles the vast majority of real-world markup but exotic/nested constructs may slip through.
- JavaScript that *runs* is left as-is; modern ES2015+ scripts will simply fail silently in IE6, which is why module scripts are dropped up front.
- POST requests and uploads are not proxied (IE6-era browsing was mostly GET).
