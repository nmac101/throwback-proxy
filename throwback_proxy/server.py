"""The HTTP proxy server: request handling, HTTPS downgrading, ad blocking,
and dispatching responses to the HTML/CSS/image rewriters."""

import os
import re
import ssl
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from html import unescape as html_unescape
from urllib.parse import urljoin, urlparse

from .ads import _is_ad_url
from .config import (DEFAULT_PORT, FIX_IE_CSS_LIMIT_JS,
                     FIX_IE_CSS_LIMIT_PATH, HOST, TRANSPARENT_GIF,
                     XHTML_STRICT_DOCTYPE)
from .css import _body_gradient_filter, transform_css
from .fetch import http_get
from .html import transform_html
from .images import _svg_to_png, _webp_to_png
from .layout import (_apply_style_edits, _bg_image_classes,
                     _bg_image_elements, _bottom_pin_elements,
                     _clearfix_floated_parents, _container_metrics,
                     _flex_container_classes, _flex_row_layout,
                     _full_width_classes,
                     _float_classes, _fixed_px_widths,
                     _grid_column_classes, _grid_row_layout,
                     _margin_auto_classes, _pin_fixed_elements,
                     _pinned_classes, _row_container_widths,
                     _viewport_classes, _viewport_elements)
from .urls import from_proxy, to_proxy


try:
    from http.server import ThreadingHTTPServer  # py>=3.7
except ImportError:  # pragma: no cover
    from socketserver import ThreadingMixIn
    from http.server import HTTPServer

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        pass


class _Server(ThreadingHTTPServer):
    # A browser loading one page fires dozens of concurrent asset requests;
    # the default listen backlog of 5 overflows and drops connections, which
    # looks like the proxy "hanging".  Raise it well above a page's fan-out.
    request_queue_size = 128


def _make_tls_context():
    """Self-signed cert via the openssl CLI, cached in a temp dir."""
    d = os.path.join(tempfile.gettempdir(), "throwback-proxy-tls")
    os.makedirs(d, exist_ok=True)
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        try:
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", key, "-out", cert, "-days", "3650",
                    "-nodes", "-subj", "/CN=ThrowbackProxy",
                ],
                check=True, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        return ctx
    except (ssl.SSLError, OSError):
        return None

LANDING = XHTML_STRICT_DOCTYPE + """
<html><head><title>Throwback Proxy</title></head>
<body bgcolor="#ffffff" text="#000000">
<center>
<h1>Throwback Proxy</h1>
<p>Rewrites modern web pages for Internet Explorer 6.</p>
<p>This proxy only works when configured as your <b>system / LAN proxy</b>.</p>
<p>Set your browser's proxy to <tt>__ADDR__</tt> (Internet Options &rarr;
Connections &rarr; LAN settings &rarr; Proxy) and then browse normally.</p>
<p style="font-size:11px;color:#666666">
All pages are rewritten for IE6 (HTML4, no CSS3, no flexbox), HTTPS is
downgraded to HTTP, and ads are blocked.
</p>
</center>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ThrowbackProxy/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _is_foreign_host(self):
        """True when the Host header points at a site other than this proxy.

        We are bound to 0.0.0.0, so direct browsing may arrive with any of
        our IPs in the Host header; treat anything on our port (or loopback)
        as local, and everything else as proxied traffic.
        """
        host = (self.headers.get("Host") or "").lower()
        if not host:
            return False
        hostname, _, port = host.partition(":")
        if hostname in ("localhost", "127.0.0.1"):
            return False
        try:
            if int(port) == self.server.server_address[1]:
                return False
        except ValueError:
            pass
        return True

    def _abs_base(self):
        """Flag/URL for system-proxy mode (absolute-form, foreign-Host,
        CONNECT-tunnel requests). Non-None tells the rewriters to keep
        original absolute URLs instead of wrapping them in the proxy path
        scheme -- the browser routes all http traffic through us anyway."""
        return "http://%s:%d" % (
            self.client_address[0], self.server.server_address[1]
        )

    # -- helpers ------------------------------------------------------------
    def _send(self, body, ctype, status=200, head_only=False, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        if "charset" not in ctype and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- verbs --------------------------------------------------------------
    def do_GET(self):
        self._handle(head_only=False)

    def do_HEAD(self):
        self._handle(head_only=True)

    def do_POST(self):
        self._send("<html><body>POST is not proxied by Throwback Proxy.</body></html>",
                   "text/html", 405)

    def do_CONNECT(self):
        # System-proxy mode: the browser tunnels an HTTPS site through us.
        # Answer 200, terminate TLS with our self-signed cert, then let the
        # handle() loop read the real (origin-form) request over the tunnel;
        # it is downgraded to plain HTTP upstream.
        self.send_response_only(200)
        self.end_headers()
        ctx = getattr(self.server, "ssl_ctx", None)
        if ctx is None:
            self.wfile.write(
                b"CONNECT failed: openssl not found (no TLS support)\r\n"
            )
            self.close_connection = True
            return
        try:
            tls = ctx.wrap_socket(self.connection, server_side=True)
        except (ssl.SSLError, OSError):
            self.close_connection = True
            return
        self.connection = tls
        self.rfile = tls.makefile("rb", self.rbufsize)
        self.wfile = tls.makefile("wb")
        self.close_connection = False
        self._tls = True  # request arrived over a CONNECT tunnel (https)

    # -- main ---------------------------------------------------------------
    def _handle(self, head_only):
        # Internal static assets (injected into every proxied page); match in
        # both origin-form (/path) and absolute-form (http://host/path).
        if urlparse(self.path).path == FIX_IE_CSS_LIMIT_PATH:
            self._send(FIX_IE_CSS_LIMIT_JS, "application/javascript", 200,
                       head_only,
                       {"Cache-Control": "max-age=86400"})
            return
        abs_base = None
        # Absolute-form request target ("GET http://google.com/ HTTP/1.1").
        if self.path.lower().startswith(("http://", "https://")):
            url = self.path
            # Tolerate a doubled proxy path ("http://host/http/host2/..."):
            # the browser resolved one of our rewritten links against the
            # origin site instead of the proxy.
            m = re.match(r"^https?://[^/]+(/.*)$", self.path, re.I)
            if m and from_proxy(m.group(1)):
                url = from_proxy(m.group(1))
            abs_base = self._abs_base()
        elif self._is_foreign_host():
            # Origin-form request from a system proxy (after CONNECT or as a
            # plain proxied request): the target is in the Host header.
            url = "http://%s%s" % (
                self.headers.get("Host"), self.path
            )
            abs_base = self._abs_base()
        else:
            # Direct URLs (http://proxy:port/http://host/...) are not a thing;
            # this proxy is only meant to be used as a system/LAN proxy.
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(
                    LANDING.replace(
                        "__ADDR__",
                        "%s:%d" % (
                            self.client_address[0],
                            self.server.server_address[1],
                        ),
                    ),
                    "text/html",
                    head_only=head_only,
                )
                return
            self._send(
                XHTML_STRICT_DOCTYPE +
                "<html><body>Not found.<br>This proxy is only used via "
                "system/LAN proxy settings, not by typing proxy URLs.</body>"
                "</html>",
                "text/html",
                404,
                head_only,
            )
            return

        # Downgrade HTTPS: redirect the client to the http:// version so it
        # reloads over plain HTTP (no TLS, no self-signed-cert prompts).  The
        # request was https if it was absolute-form https:// or arrived over a
        # CONNECT tunnel (self._tls).
        if self.path.lower().startswith("https://") \
                or getattr(self, "_tls", False):
            loc = url if url.lower().startswith("http://") \
                else url.replace("https://", "http://", 1)
            self._redirect(loc)
            return

        # Optional ?ie6w=<px> hint (added by the proxy itself when it injects
        # a background <img>): render the SVG at that width so IE6 shows the
        # PNG 1:1.  Stripped before the upstream fetch.
        svg_width = None
        _p = urlparse(url)
        if _p.query:
            _kept, _w = [], None
            for _pair in _p.query.split("&"):
                if _pair.startswith("ie6w="):
                    _w = _pair.split("=", 1)[1]
                else:
                    _kept.append(_pair)
            if _w and _w.isdigit():
                svg_width = int(_w)
            url = _p._replace(query="&".join(_kept)).geturl()

        # --- ad blocking (no upstream request at all) ---
        if _is_ad_url(url):
            p = urlparse(url)
            if re.search(r"\.(gif|png|jpe?g|webp|svg)$", p.path, re.I):
                self._send(TRANSPARENT_GIF, "image/gif", 204 if head_only else 200,
                           head_only)
            else:
                self._send(b"", "text/plain", 204, head_only)
            return

        # Downgrade HTTPS to HTTP for the upstream fetch (IE6-era trust).
        url = re.sub(r"^https://", "http://", url)

        # --- fetch upstream ---
        status, headers, body = http_get(url, timeout=20)
        if status is None:
            self._send(
                b"Upstream error: could not reach " + url.encode("utf-8", "replace"),
                "text/plain", 502, head_only,
            )
            return
        if 300 <= status < 400 and headers.get("Location"):
            loc = urljoin(url, headers["Location"])
            self._redirect(to_proxy(loc, abs_base))
            return
        ctype = headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        charset_m = re.search(r"charset=([\w-]+)", headers.get("Content-Type") or "", re.I)
        charset = charset_m.group(1).lower() if charset_m else "utf-8"

        # --- transform by type ---
        if ctype == "text/html":
            text = body.decode(charset, errors="replace")
            # Find row-flex containers (from <style> blocks and linked
            # stylesheets) so their children can be laid out side by side.
            flex_classes = set()
            widths = {}
            grid_containers = {}
            grid_areas = {}
            pinned = set()
            vp = {}
            bg = {}
            bottom = set()
            float_classes = set()
            full_width = set()
            row_widths = {}
            body_filter = None
            css_chunks = []
            for sm in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>",
                                  text, re.I | re.S):
                css_chunks.append(sm.group(1))
                flex_classes |= _flex_container_classes(sm.group(1))
                widths.update(_fixed_px_widths(sm.group(1)))
                gc, ga = _grid_column_classes(sm.group(1))
                for k, v in gc.items():
                    grid_containers.setdefault(k, v)
                for k, v in ga.items():
                    grid_areas.setdefault(k, set()).update(v)
                pinned |= _pinned_classes(sm.group(1))
                for k, v in _viewport_classes(sm.group(1)).items():
                    vp.setdefault(k, []).extend(v)
                # on the TRANSFORMED css: the image url may live in a
                # var() that only flattening/resolution exposes
                bg.update(_bg_image_classes(
                    transform_css(sm.group(1), url, abs_base)))
                bottom |= _margin_auto_classes(sm.group(1))
                float_classes |= _float_classes(sm.group(1))
                full_width |= _full_width_classes(sm.group(1))
                row_widths.update(_row_container_widths(sm.group(1)))
                body_filter = body_filter or _body_gradient_filter(sm.group(1))
            for lm in re.finditer(r"<link\b[^>]*>", text, re.I):
                tag = lm.group(0)
                if not re.search(r'rel\s*=\s*["\']?stylesheet', tag, re.I):
                    continue
                hm = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.I)
                if not hm:
                    continue
                su = re.sub(r"^https://", "http://",
                            urljoin(url, html_unescape(hm.group(1))))
                if _is_ad_url(su):
                    continue
                _, _, sdata = http_get(su, timeout=5)
                if sdata:
                    scss = sdata.decode("utf-8", "replace")
                    css_chunks.append(scss)
                    flex_classes |= _flex_container_classes(scss)
                    widths.update(_fixed_px_widths(scss))
                    gc, ga = _grid_column_classes(scss)
                    for k, v in gc.items():
                        grid_containers.setdefault(k, v)
                    for k, v in ga.items():
                        grid_areas.setdefault(k, set()).update(v)
                    pinned |= _pinned_classes(scss)
                    for k, v in _viewport_classes(scss).items():
                        vp.setdefault(k, []).extend(v)
                    bg.update(_bg_image_classes(
                        transform_css(scss, su, abs_base)))
                    bottom |= _margin_auto_classes(scss)
                    float_classes |= _float_classes(scss)
                    full_width |= _full_width_classes(scss)
                    row_widths.update(_row_container_widths(scss))
                    body_filter = body_filter or _body_gradient_filter(scss)
            text = transform_html(text, url, abs_base)

            def _style_block(m):
                return "<style>%s</style>" % transform_css(
                    m.group(1), url, abs_base)

            # Inline <style> blocks need the same treatment as linked
            # stylesheets (nmac.me-style external CSS alone never exposed
            # this; sites with inline CSS like example.com do).
            text = re.sub(
                r"<style\b[^>]*>(.*?)</style\s*>",
                _style_block, text, flags=re.I | re.S)
            text = _bg_image_elements(text, bg, url, abs_base)
            if body_filter:
                # IE6 can't filter body: paint the gradient on a pinned,
                # full-viewport div behind everything (z-index:-1 renders
                # above the body background color in IE6).
                pinned.add("ie6bg")
                vp.setdefault("ie6bg", []).extend(
                    [("width", 1.0), ("height", 1.0)]
                )
                bgdiv = (
                    '<div class="ie6bg" style="position:absolute;left:0;top:0;'
                    'width:100%%;height:100%%;z-index:-1;filter:%s"></div>'
                    % body_filter
                )
                text = re.sub(
                    r"(<body\b[^>]*>)", r"\1" + bgdiv, text, count=1, flags=re.I
                )
            text = _flex_row_layout(text, flex_classes, widths, pinned,
                                    full_width, row_widths)
            text = _grid_row_layout(text, grid_containers, grid_areas)
            text = _apply_style_edits(text, _clearfix_floated_parents(
                text, float_classes))
            text = _pin_fixed_elements(text, pinned)
            text = _viewport_elements(text, vp)
            text = _bottom_pin_elements(
                text, bottom, pinned,
                _container_metrics("\n".join(css_chunks), pinned))
            self._send(text, "text/html", 200, head_only)
            return
        if ctype == "text/css":
            text = body.decode(charset, errors="replace")
            text = transform_css(text, url, abs_base)
            self._send(text, "text/css", 200, head_only)
            return
        if ctype == "image/webp" and _webp_to_png:
            body = _webp_to_png(body)
            ctype = "image/png"
        if (ctype == "image/svg+xml" or url.lower().endswith(".svg")) \
                and _svg_to_png:
            png = _svg_to_png(body, svg_width)
            if png:
                body, ctype = png, "image/png"

        self._send(body, ctype, 200, head_only)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = _Server((HOST, port), Handler)
    # Let a handler thread stuck on a slow upstream die with the process
    # instead of blocking Ctrl-C / shutdown.
    srv.daemon_threads = True
    srv.ssl_ctx = _make_tls_context()
    print("Throwback Proxy listening on http://%s:%d/" % (HOST, port))
    print("Set it as your system/LAN proxy: <this machine's IP>:%d" % port)
    if srv.ssl_ctx is None:
        print(
            "Note: openssl not found -- CONNECT (HTTPS sites in "
            "system-proxy mode) is disabled."
        )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
