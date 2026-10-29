"""Regression tests: python3 -m unittest discover -s tests"""

import threading
import base64
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from throwback_proxy.css import _eval_calc, _replace_calc, transform_css
from throwback_proxy.html import transform_html
from throwback_proxy.images import _ie6_png
from throwback_proxy.layout import _flex_container_classes, _flex_row_layout
from throwback_proxy.selectors import apply_synthetic_classes
from throwback_proxy.server import Handler, _decode, _Server

DOCTYPE = "<!DOCTYPE html PUBLIC"


class CssTests(unittest.TestCase):
    def test_rgba_all_occurrences_converted(self):
        css = "".join("a%d{color:rgba(1,2,3,.5)}" % i for i in range(5))
        self.assertNotIn("rgba", transform_css(css, "http://x/"))
        css = "".join("a%d{color:rgba(1,2,3)}" % i for i in range(5))
        self.assertNotIn("rgba", transform_css(css, "http://x/"))

    def test_calc(self):
        self.assertEqual(_replace_calc("calc(10px + 5px)"), "15px")
        self.assertEqual(_replace_calc("calc(100% - 20px)"), "100%")
        self.assertEqual(_replace_calc("calc((100% - 20px) / 2)"), "auto")
        self.assertEqual(_eval_calc("9**9**9**9"), "auto")  # no huge powers
        self.assertNotIn("(", _replace_calc("calc((1px + 2em) * 3)"))


class WikiRegressionTests(unittest.TestCase):
    """Bugs seen rendering MediaWiki's Vector skin in IE6."""

    def test_inherit_degraded(self):
        # IE6 ignores `inherit`; nested headings would compound font sizes
        out = transform_css(".w h2{font:inherit;color:inherit}", "http://x/")
        self.assertNotIn("inherit", out)
        self.assertIn("font-size: 100%", out)

    def test_vendor_pseudo_rule_dropped_not_applied_to_element(self):
        out = transform_css(
            ".q::-webkit-search-cancel-button,.q::placeholder{display:none}"
            ".q{color:red}", "http://x/")
        self.assertNotIn("display", out)
        self.assertIn("color: red", out)

    def test_chained_class_and_attr_selectors_become_synthetic(self):
        synth = {}
        css = transform_css(
            ".tabs .watch.icon a{height:0} input[name='go']{width:9px}",
            "http://x/", synth=synth)
        self.assertNotIn(".watch.icon", css)
        self.assertNotIn("[", css)
        html = apply_synthetic_classes(
            '<li class="icon"><a>x</a></li><li class="watch icon">'
            '<a>y</a></li><input name="go"><input name="no">', synth)
        self.assertEqual(html.count("ie6x"), 2)  # only the real matches
        self.assertNotIn('<li class="icon ie6x', html)

    def test_flex_rule_scoped_to_ancestor(self):
        flex = _flex_container_classes(
            ".user-menu .list{display:flex} .row{display:flex}")
        html = ('<div class="user-menu"><ul class="list"><li>a</li><li>b'
                '</li></ul></div><div id="side"><ul class="list"><li>c</li>'
                "</ul></div>")
        out = _flex_row_layout(html, flex, {})
        self.assertIn('<li style="float:left;display:block;">a', out)
        self.assertIn("<li>c</li>", out)

    def test_background_size_syntax_stripped(self):
        out = transform_css(".b{background:no-repeat center / 1em url(a.png)}",
                            "http://x/")
        self.assertNotIn("/ 1em", out)

    def test_text_indent_hidden_labels(self):
        out = transform_css(".b{text-indent:-99999px;font-size:.8em}",
                            "http://x/")
        self.assertIn("font-size: 0", out)


@unittest.skipIf(_ie6_png is None, "optional Pillow dependency is unavailable")
class PngAlphaTests(unittest.TestCase):
    def _png(self, img):
        import io
        b = io.BytesIO()
        img.save(b, "PNG")
        return b.getvalue()

    def test_rgba_becomes_8bit_binary_transparency(self):
        import io
        from PIL import Image
        from throwback_proxy.images import _ie6_png
        img = Image.new("RGBA", (4, 1))
        img.putdata([(255, 0, 0, 255), (0, 0, 0, 0),
                     (0, 0, 255, 255), (0, 255, 0, 40)])
        out = Image.open(io.BytesIO(_ie6_png(self._png(img))))
        self.assertEqual(out.mode, "P")
        rgba = out.convert("RGBA")
        self.assertEqual(rgba.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(rgba.getpixel((1, 0))[3], 0)   # transparent kept
        self.assertEqual(rgba.getpixel((3, 0))[3], 0)   # faint -> clear
        self.assertEqual(rgba.getpixel((2, 0))[3], 255)

    def test_opaque_and_8bit_untouched(self):
        from PIL import Image
        from throwback_proxy.images import _ie6_png
        self.assertIsNone(_ie6_png(self._png(Image.new("RGB", (2, 2)))))
        p = Image.new("P", (2, 2))
        self.assertIsNone(_ie6_png(self._png(p)))


class ArchWikiRegressionTests(unittest.TestCase):
    """Bugs seen rendering MediaWiki's Vector 2022 skin in IE6."""

    def test_media_queries_evaluated_not_stripped(self):
        css = ("@media (min-width:1120px){.a{x:1}} @media (max-width:700px)"
               "{.b{x:2}} @media print{.c{x:3}} @media screen{.d{x:4}}")
        out = transform_css(css, "http://x/")
        self.assertIn(".a", out)
        self.assertIn(".d", out)
        self.assertNotIn(".b", out)
        self.assertNotIn(".c", out)

    def test_light_dark_and_masks(self):
        out = transform_css(
            ".a{color:light-dark(#fff,rgb(0,0,0))}"
            ".i{background-color:#000;mask-image:url(/i.svg)}", "http://x/")
        self.assertIn("color: #fff", out)
        self.assertIn("background-image: url(", out)
        self.assertIn("background-color: transparent", out)

    def test_sibling_and_structural_selectors(self):
        syn = {}
        css = transform_css(
            ".btn span + span{display:none} li:first-child{x:1}",
            "http://x/", synth=syn)
        self.assertNotIn("+", css)
        self.assertNotIn(":first-child", css)
        html = apply_synthetic_classes(
            '<a class="btn"><span>1</span><span>2</span></a>'
            "<ul><li>a</li><li>b</li></ul>", syn)
        self.assertIn('<span class="ie6x', html)
        self.assertEqual(html.count("ie6x"), 2)  # 2nd span + 1st li

    def test_later_display_overrides_flex(self):
        flex = _flex_container_classes(
            ".h{display:flex} .h{display:grid} .r{display:flex}")
        self.assertEqual(set(flex), {"r"})

    def test_justify_end_pins_last_child_right(self):
        flex = _flex_container_classes(
            ".bar{display:flex;justify-content:space-between}")
        out = _flex_row_layout(
            '<div class="bar"><div>a</div><div>b</div></div>', flex, {})
        self.assertIn("position:absolute;right:0", out)
        self.assertIn("position:relative", out)

    def test_flex_grow_child_fills_row(self):
        flex = _flex_container_classes(
            ".row{display:flex} .rest{flex-grow:1}")
        out = _flex_row_layout(
            '<div class="row"><div>a</div><div class="rest">b</div></div>',
            flex, {})
        self.assertIn("overflow:hidden;zoom:1", out)


class ArchWikiRound2Tests(unittest.TestCase):
    def test_universal_child_selector_is_exact(self):
        syn = {}
        css = transform_css(".cols > *{display:inline-block;width:100%}",
                            "http://x/", synth=syn)
        self.assertIn("display: block", css)  # full-width inline-block
        html = apply_synthetic_classes(
            '<div class="cols"><dl><dt>a</dt></dl></div>', syn)
        self.assertIn('<dl class="ie6x', html)
        self.assertNotIn('<dt class', html)  # grandchildren untouched

    def test_negative_margin_gets_position_relative(self):
        out = transform_css("#nav{margin-left:-8px}", "http://x/")
        self.assertIn("position: relative", out)

    def test_max_width_container_capped(self):
        from throwback_proxy.layout import (_apply_max_widths,
                                            _max_width_classes)
        mw = _max_width_classes(".box{max-width:31.25rem} .big{max-width:900px}")
        self.assertEqual(mw, {"box": 500})
        out = _apply_max_widths('<div class="box">x</div><b class="box">y</b>',
                                mw)
        self.assertIn('<div class="box" style="width:500px;">', out)
        self.assertIn('<b class="box">', out)

    def test_grid_sibling_beside_float_is_bfc(self):
        from throwback_proxy.layout import (_grid_column_classes,
                                            _grid_row_layout)
        gc, ga = _grid_column_classes(
            ".g{display:grid;grid-template-columns:200px 1fr;"
            "grid-template-areas:'side main'} .s{grid-area:side}"
            " .g{grid-template-columns:300px 1fr}")
        self.assertEqual(gc["g"]["width"], 300)  # later rule wins
        out = _grid_row_layout(
            '<div class="g"><div class="s">a</div><div>b</div></div>', gc, ga)
        self.assertIn("float:left;width:300px", out)
        self.assertIn("overflow:hidden;zoom:1", out)


class HtmlTests(unittest.TestCase):
    def test_doctype_first_and_prolog_removed(self):
        out = transform_html('﻿<?xml version="1.0"?>\n<!-- c -->'
                             "<!DOCTYPE html><html><head></head></html>",
                             "http://a/")
        self.assertTrue(out.startswith(DOCTYPE))
        self.assertNotIn("<?xml", out)

    def test_doctype_added_when_missing(self):
        out = transform_html("<html><head></head></html>", "http://a/")
        self.assertTrue(out.startswith(DOCTYPE))

    def test_charset_forced_to_utf8(self):
        out = transform_html(
            '<!DOCTYPE html><html><head><meta charset="iso-8859-1">'
            '<meta http-equiv="Content-Type" content="text/html; '
            'charset=windows-1252"></head></html>', "http://a/")
        self.assertNotIn("iso-8859-1", out.lower())
        self.assertNotIn("windows-1252", out.lower())

    def test_meta_content_text_untouched(self):
        out = transform_html('<html><head><meta name="description" '
                             'content="hello world"></head></html>',
                             "http://a/")
        self.assertIn('content="hello world"', out)

    def test_json_script_not_mangled_and_svg_dropped(self):
        out = transform_html(
            '<html><head></head><body><svg><title>icon</title></svg>'
            '<script type="application/ld+json">{"a":"let us"}</script>'
            "<script>let a=1;</script></body></html>", "http://a/")
        self.assertNotIn("icon", out)
        self.assertIn('"let us"', out)
        self.assertIn("var a=1;", out)

    def test_head_tag_not_confused_with_header(self):
        out = transform_html("<html><body><header>x</header></body></html>",
                             "http://a/")
        self.assertNotIn("<div><meta", out)


class DecodeTests(unittest.TestCase):
    def test_meta_charset_and_bad_names(self):
        body = '<meta charset="iso-8859-1">caf\xe9'.encode("latin-1")
        self.assertIn("caf\xe9", _decode(body, None))
        self.assertEqual(_decode(b"abc", "no-such-charset"), "abc")


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/latin":
            body = ('<html><head><meta charset="iso-8859-1"></head>'
                    "<body>caf\xe9</body></html>").encode("latin-1")
            ctype = "text/html"
        elif self.path == "/xhtml":
            body, ctype = b"<html><head></head><body>x</body></html>", \
                "application/xhtml+xml"
        elif self.path == "/audit":
            body = (b'<html><head><base href="/assets/">'
                    b'<link rel=stylesheet href=main.css>'
                    b'<style media=print>.row{display:flex}</style>'
                    b'<style>.row{display:block}.box > p{color:red}</style>'
                    b'</head><body><div class="row"><div>a</div><div>b</div></div>'
                    b'<div class="box"><p>direct</p><div><p>nested</p></div></div>'
                    b'<script>var s="<section><style>.fake{color:red}</style></section>";'
                    b'</script></body></html>')
            ctype = 'Text/HTML; charset="utf-8"'
        elif self.path == "/assets/main.css":
            body, ctype = b'.row{display:flex}', 'text/css'
        elif self.path == "/sizing":
            body = (b'<html><head><style>.tabs a{box-sizing:border-box;'
                    b'height:3em;padding-top:1em}</style></head>'
                    b'<body><div class="tabs"><a href="/latin">Tab</a></div>'
                    b'<a href="/latin">Other link</a></body></html>')
            ctype = 'text/html'
        elif self.path == "/search.svg?a45d6":
            body = (b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
                    b'<rect width="10" height="10"/></svg>')
            # Some origins mislabel SVG; the path check must ignore queries.
            ctype = 'application/octet-stream'
        elif self.path == "/mediawiki":
            body = (b'<html><head><meta name="generator" content="MediaWiki 1.46">'
                    b'<style>.toc{display:table}.mw-jump-link:not(:focus){position:absolute}</style>'
                    b'</head><body><a class="mw-jump-link" href="#toc">Jump</a>'
                    b'<div id="toc" class="toc"><div>Contents</div><ul><li>Section</li></ul>'
                    b'</div><h2>Section</h2></body></html>')
            ctype = 'text/html'
        elif self.path == "/redir":
            self.send_response(301)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"no")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if self.path == "/search.svg?a45d6":
            self.send_header('Last-Modified', 'Tue, 30 Jun 2026 12:29:04 GMT')
            self.send_header('ETag', '"upstream-svg"')
        self.end_headers()
        self.wfile.write(body)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        cls.proxy = _Server(("127.0.0.1", 0), Handler)
        cls.proxy.ssl_ctx = None
        cls.proxy.daemon_threads = True
        for srv in (cls.up, cls.proxy):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.up.shutdown()
        cls.proxy.shutdown()
        cls.up.server_close()
        cls.proxy.server_close()

    def get(self, path, method="GET", body=None, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.up.server_address[1], path)
        # Connect explicitly so HTTP_PROXY/NO_PROXY cannot bypass the proxy.
        connection = HTTPConnection(*self.proxy.server_address, timeout=10)
        try:
            connection.request(method, url, body=body, headers=headers or {})
            with connection.getresponse() as r:
                return r.status, r.headers, r.read()
        finally:
            connection.close()

    def test_latin1_page_decoded(self):
        status, headers, body = self.get("/latin")
        self.assertEqual(status, 200)
        self.assertIn("caf\xe9", body.decode("utf-8"))

    def test_xhtml_served_as_html(self):
        status, headers, body = self.get("/xhtml")
        self.assertEqual(headers.get_content_type(), "text/html")

    def test_redirect_relayed(self):
        status, headers, body = self.get("/redir")
        self.assertEqual(status, 302)
        self.assertTrue(headers["Location"].endswith("/final"))

    def test_upstream_status_preserved(self):
        self.assertEqual(self.get("/missing")[0], 404)

    def test_styles_selectors_and_script_text_through_proxy(self):
        status, headers, body = self.get('/audit')
        self.assertEqual(status, 200)
        out = body.decode('utf-8')
        self.assertIn('<div class="row"><div>a</div><div>b</div></div>', out)
        self.assertIn('<p class="ie6x', out)
        self.assertIn('<p>nested</p>', out)
        self.assertIn('<style media="print">', out)
        self.assertIn('"<section><style>.fake{color:red}</style></section>"', out)
        self.assertNotIn('\x00', out)

    def test_head_matches_get_headers_without_body(self):
        _, get_headers, _ = self.get('/latin')
        status, headers, body = self.get('/latin', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Length'], get_headers['Content-Length'])
        self.assertEqual(body, b'')

    def test_box_sizing_uses_scoped_event_pass_without_expressions(self):
        status, _, body = self.get('/sizing')
        self.assertEqual(status, 200)
        html = body.decode('utf-8')
        self.assertNotIn('expression(', html)
        self.assertIn('window.__tbInitSizing([', html)
        self.assertIn('<a class="ie6x', html)
        self.assertNotIn('class="ie6x', html[html.index('Tab</a>'):html.index('Other link')])

    def test_mediawiki_components_through_proxy(self):
        status, _, body = self.get('/mediawiki')
        self.assertEqual(status, 200)
        html = body.decode('utf-8')
        self.assertIn('float:left;width:auto;display:block;', html)
        self.assertIn('class="mw-jump-link tb-ie6-skip-hidden"', html)
        self.assertIn('href="#toc"', html)
        self.assertIn("attachEvent('onfocus',show)", html)
        self.assertIn('clear:left', html)
        self.assertNotIn('\x00', html)

    @patch('throwback_proxy.server._svg_to_png')
    def test_versioned_svg_is_a_cacheable_png_with_downstream_validator(self, convert):
        convert.return_value = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8'
            'AAQUBAScY42YAAAAASUVORK5CYII=')
        status, headers, data = self.get('/search.svg?a45d6')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(headers['Content-Length'], str(len(data)))
        self.assertEqual(headers['Cache-Control'], 'private, max-age=60')
        self.assertEqual(headers['Last-Modified'], 'Tue, 30 Jun 2026 12:29:04 GMT')
        self.assertNotEqual(headers['ETag'], '"upstream-svg"')
        self.assertTrue(convert.call_args.args[0].startswith(b'<svg'))
        self.assertIsNone(convert.call_args.args[1])
        status, again, empty = self.get('/search.svg?a45d6',
                                      headers={'If-None-Match': 'W/' + headers['ETag']})
        self.assertEqual(status, 304)
        self.assertEqual(empty, b'')
        self.assertEqual(again['ETag'], headers['ETag'])
        self.assertNotIn('Content-Length', again)
        status, head, empty = self.get('/search.svg?a45d6', method='HEAD')
        self.assertEqual(head['Content-Length'], str(len(data)))
        self.assertEqual(head['ETag'], headers['ETag'])
        self.assertEqual(empty, b'')

    def test_post_closes_connection_with_unread_body(self):
        status, headers, body = self.get('/latin', method='POST', body=b'data')
        self.assertEqual(status, 405)
        self.assertEqual(headers['Connection'], 'close')
        self.assertEqual(headers['Allow'], 'GET, HEAD')


if __name__ == "__main__":
    unittest.main()
