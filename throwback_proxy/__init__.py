"""
Throwback Proxy -- an HTTP proxy that rewrites modern web pages so they can
be rendered by Internet Explorer 6, while keeping the original structure,
and blocks ads along the way.

Usage:
    python3 -m throwback_proxy [port]          (default port: 8080)

Then set it as your system/LAN proxy (this machine's IP, port 8080).
Absolute-form requests are proxied directly, and HTTPS requests
(absolute-form or over a CONNECT tunnel) are redirected to their HTTP
equivalent. There are no direct proxy URLs -- the browser must use
it as its configured proxy.

How it works:
  * Every response is fetched upstream and, depending on its type:
      - text/html : HTML5 tags are down-converted (section->div, ...),
                    <canvas>/<video>/<audio> are removed, ES-module scripts
                    are dropped, ad scripts are stripped, and every
                    src/href/poster/style-url() is rewritten to go through
                    this proxy.
      - text/css  : @media/@supports/@font-face/keyframes are removed,
                    flexbox rows are approximated with inline-block children,
                    position:fixed becomes absolute, pinned by a JS
                    scroll handler (CSS expressions don't run in the
                    standards mode the proxy forces via the doctype),
                    gradients/shadows/opacity become DXImageTransform
                    filters, rgba()/hsl() become rgb(), calc()/vw/vh are
                    approximated, unsupported properties and selectors are
                    dropped, and url() references are rewritten through the
                    proxy.
      - image/webp: converted to PNG on the fly (if Pillow is installed).
      - image/svg+xml: converted to PNG on the fly (if ImageMagick is
        installed); IE6 cannot render SVG.
  * HTTPS requests are redirected (302) to their HTTP equivalent.
  * Requests whose host matches a known ad/tracking domain are served an
    empty response (204) or a transparent 1x1 GIF, without touching the
    network at all.

Only the Python standard library is required. Pillow is optional (webp),
ImageMagick optional (svg);
the openssl CLI is optional (enables CONNECT/TLS for system-proxy mode).

Modules:
  config  -- global constants (host, port, user agent, ad domains, ...)
  urls    -- the proxy URL scheme (to_proxy / from_proxy)
  css     -- CSS rewriting
  html    -- HTML rewriting
  layout  -- layout fixes (flex/grid/fixed-pinning/viewport)
  fetch   -- upstream HTTP fetching
  images  -- webp/svg -> PNG conversion
  ads     -- ad / tracking domain matching
  server  -- the HTTP proxy server and entry point
"""

__version__ = "1.0"
