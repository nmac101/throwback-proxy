# throwback-proxy

An HTTP proxy in pure Python (stdlib only) that rewrites modern web pages so
they can be rendered by **Internet Explorer 6**, mostly preserving the original
page structure — and blocks ads on the way.

## Run

```sh
python3 ie6proxy.py [port]     # default: 8080
```

Then browse to `http://127.0.0.1:8080/` (landing page with a URL box), or
directly to the target URL as a path, e.g.
`http://127.0.0.1:8080/http://example.com/`.

Proxy URL scheme: `/<scheme>/<host[:port]>/<path>?<query>` — the legacy
`/p/<scheme>/...` prefix and absolute-form requests (`GET http://example.com/`)
are accepted as well.

## Use it as your system / LAN proxy

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

### HTTPS downgrade
All upstream fetches use plain HTTP: a `/https/host/...` link is fetched from `http://host/...`. (Links in rewritten pages still carry their original scheme, so mixed content behaves as it would on the open web.)

### Ad blocking
Requests whose host matches a known ad/tracking domain (doubleclick, googlesyndication, google-analytics, googletagmanager, adnxs, taboola, outbrain, criteo, pubmatic, rubiconproject, ...) are served locally without any upstream request: an empty `204` for scripts/XHR, a transparent 1×1 GIF for image URLs. The list lives in `AD_DOMAINS` in `ie6proxy.py`.

## Caveats
- Rewriting is regex-based (no full parser): it handles the vast majority of real-world markup but exotic/nested constructs may slip through.
- JavaScript that *runs* is left as-is; modern ES2015+ scripts will simply fail silently in IE6, which is why module scripts are dropped up front.
- POST requests and uploads are not proxied (IE6-era browsing was mostly GET).
