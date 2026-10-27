"""Regression tests: python3 -m unittest discover -s tests"""

import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from throwback_proxy.css import _eval_calc, _replace_calc, transform_css
from throwback_proxy.html import transform_html
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
        cls.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(
                {"http": "http://127.0.0.1:%d" % cls.proxy.server_address[1]}),
            _NoRedirect())

    @classmethod
    def tearDownClass(cls):
        cls.up.shutdown()
        cls.proxy.shutdown()

    def get(self, path):
        url = "http://127.0.0.1:%d%s" % (self.up.server_address[1], path)
        try:
            r = self.opener.open(url, timeout=10)
        except urllib.error.HTTPError as e:
            r = e
        return r.status, r.headers, r.read()

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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


if __name__ == "__main__":
    unittest.main()
