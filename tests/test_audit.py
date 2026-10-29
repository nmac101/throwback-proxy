"""Regressions found auditing against ie6reference.md."""

import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from throwback_proxy.css import transform_css
from throwback_proxy.fetch import _get_once, http_get
from throwback_proxy.html import document_styles, transform_html
from throwback_proxy.selectors import apply_synthetic_classes
from throwback_proxy.server import Handler, _page_css_hints
from throwback_proxy.urls import from_proxy, to_proxy


class CssAuditTests(unittest.TestCase):
    def test_cyclic_and_expanding_vars_terminate(self):
        # Run out of process so a regression reports a failure, not a hung
        # test runner (the original resolver looped on alternating cycles).
        result = subprocess.run([sys.executable, "-c", '''
from throwback_proxy.css import transform_css
css = ':root{--a:var(--b);--b:var(--a)}p{color:var(--a,red)}'
assert 'color: red' in transform_css(css, 'http://x/')
css = ':root{--a:var(--a) var(--a)}p{color:var(--a,blue)}'
assert 'color: blue' in transform_css(css, 'http://x/')
css = ':root{--v0:x;' + ';'.join('--v%d:var(--v%d) var(--v%d)' %
    (i,i-1,i-1) for i in range(1,40)) + '}p{color:var(--v39,red)}'
assert len(transform_css(css, 'http://x/')) < 70000
'''], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nested_var_fallback_and_invalid_value(self):
        css = transform_css('p{color:var(--missing,var(--also-missing,rgb(1,2,3)));'
                            'border:1px solid var(--missing)}', 'http://x/')
        self.assertIn('color: rgb(1,2,3)', css)
        self.assertNotIn('border', css)

    def test_font_shorthand_keeps_families_and_reset_semantics(self):
        for font in ('italic small-caps bold 16px/1.5 "Times New Roman", serif',
                     'small Arial, sans-serif', 'menu'):
            with self.subTest(font=font):
                self.assertIn('font: ' + font,
                              transform_css('p{font:' + font + '}', 'http://x/'))

    def test_last_font_shorthand_resets_earlier_weight(self):
        css = transform_css('p{font:12px serif;font-weight:bold;font:16px Arial}',
                            'http://x/')
        self.assertLess(css.index('font-weight'), css.index('font:'))

    def test_hsl_complete_function_and_filter_alpha(self):
        css = transform_css('p{color:HSL(120,100%,50%)}'
                            '.g{background:linear-gradient(hsla(0,100%,50%,.5),'
                            'hsl(240,100%,50%))}', 'http://x/')
        self.assertIn('color: rgb(0,255,0)', css)
        self.assertNotIn('rgb(0,255,0))', css)
        self.assertIn('#80ff0000', css)

    def test_haslayout_for_filters_and_inline_block(self):
        for body in ('opacity:.5', 'background:linear-gradient(red,blue)',
                     'display:inline-block;width:20px'):
            with self.subTest(body=body):
                self.assertIn('zoom: 1', transform_css('.a{' + body + '}', 'http://x/'))

    def test_preserve_supported_overflow_axes(self):
        css = transform_css('pre{overflow-x:auto;overflow-y:hidden}', 'http://x/')
        self.assertIn('overflow-x: auto', css)
        self.assertIn('overflow-y: hidden', css)

    def test_unsupported_not_does_not_broaden_rule(self):
        css = transform_css('.a:not(.visible),.hidden{display:none}', 'http://x/')
        self.assertNotIn('.a', css)
        self.assertIn('.hidden', css)

    def test_case_insensitive_important_wins(self):
        css = transform_css('.a{color:red ! IMPORTANT;color:blue}', 'http://x/')
        self.assertIn('red', css)
        self.assertNotIn('blue', css)


class SelectorAuditTests(unittest.TestCase):
    def rewrite(self, selector, html):
        synth = {}
        transform_css(selector + '{color:red}', 'http://x/', synth=synth)
        return apply_synthetic_classes(html, synth)

    def test_all_child_combinators_are_exact(self):
        out = self.rewrite('.box > p', '<div class="box"><p>direct</p>'
                           '<div><p>nested</p></div></div>')
        self.assertEqual(out.count('ie6x'), 1)
        self.assertIn('<p>nested</p>', out)

    def test_id_class_combination_requires_both(self):
        out = self.rewrite('#box.active', '<div id=box class=active></div>'
                           '<div class=active></div>')
        self.assertEqual(out.count('ie6x'), 1)

    def test_html5_type_selector_survives_tag_conversion(self):
        out = self.rewrite('section.feature',
                           '<section class="feature">yes</section>'
                           '<div class="feature">no</div>')
        out = transform_html(out, 'http://x/')
        self.assertIn('<div class="feature ie6x', out)
        self.assertIn('<div class="feature">no</div>', out)

    def test_unquoted_class_is_merged_not_duplicated(self):
        out = self.rewrite('.a.b', '<p class=a&#32;b data-class="keep">x</p>')
        self.assertIn('class="a b ie6x', out)
        self.assertEqual(out.count(' class='), 1)
        self.assertIn('data-class="keep"', out)

    def test_comments_scripts_and_quoted_angles_are_not_nodes(self):
        source = ('<!-- <p class="a b">fake</p> -->'
                  '<script>var s="<p class=\'a b\'>fake</p>";</script>'
                  '<textarea><p class="a b">text</p></textarea>'
                  '<p title="1 > 0" class="a b">real</p>')
        out = self.rewrite('.a.b', source)
        self.assertEqual(out.count('ie6x'), 1)
        self.assertIn('title="1 > 0" class="a b ie6x', out)

    def test_attribute_entities_and_comma_values(self):
        out = self.rewrite('[title="a,b&c"]', '<p title="a,b&amp;c">yes</p>')
        self.assertEqual(out.count('ie6x'), 1)


class DocumentAuditTests(unittest.TestCase):
    def test_scripts_and_rcdata_survive_markup_rewrites(self):
        source = ('<html><head></head><body><script>let x=1;'
                  'var s="<section src=\'x\'>let us know</section>";'
                  'var r=/const word/;</script>'
                  '<textarea><article>literal</article></textarea></body></html>')
        out = transform_html(source, 'http://x/')
        self.assertIn('var x=1;', out)
        self.assertIn('"<section src=\'x\'>let us know</section>"', out)
        self.assertIn('/const word/', out)
        self.assertIn('<textarea><article>literal</article></textarea>', out)

    def test_entities_reescaped_and_unquoted_base_honored(self):
        out = transform_html('<html><head><base href=/assets/></head>'
                             '<body><a href="a?q=&quot;x&quot;&amp;b=2">x</a>'
                             '</body></html>', 'http://x/start', 'http://proxy/')
        self.assertIn('href="http://x/assets/a?q=&quot;x&quot;&amp;b=2"', out)

    def test_relative_base_is_not_resolved_twice(self):
        out = transform_html('<html><head><base href="assets/"></head></html>',
                             'http://x/dir/page', 'http://proxy/')
        self.assertIn('href="http://x/dir/assets/"', out)
        self.assertNotIn('assets/assets', out)

    def test_query_selector_feature_detection_is_not_stubbed(self):
        out = transform_html('<html><head></head></html>', 'http://x/')
        self.assertNotIn('document.querySelectorAll=function', out)

    def test_styles_in_dom_order_with_media_and_base(self):
        source = ('<base href=/assets/><!-- <link rel=stylesheet href=comment.css> -->'
                  '<script>var s="<link rel=stylesheet href=fake.css>";</script>'
                  '<link rel=stylesheet href=main.css><style>.a{display:block}</style>'
                  '<style media=print>.a{display:flex}</style>'
                  '<link rel="alternate stylesheet" href=alternate.css>'
                  '<link rel=stylesheet disabled href=disabled.css>')
        base, sources = document_styles(source, 'http://x/start')
        self.assertEqual(base, 'http://x/assets/')
        self.assertEqual(sources, [['external', 'main.css'],
                                  ['inline', '.a{display:block}']])

    @patch('throwback_proxy.server.http_get_cached')
    def test_layout_hints_respect_sheet_order_and_charset(self, fetch):
        fetch.return_value = (200, {'Content-Type': 'text/css; charset="iso-8859-1"'},
                              '.a{display:flex}/* caf\xe9 */'.encode('latin-1'))
        base, hints = _page_css_hints('<base href=/assets/>'
                         '<link rel=stylesheet href=main.css>'
                         '<style>.a{display:block}</style>', 'http://x/', None)
        fetch.assert_called_once_with('http://x/assets/main.css', timeout=5)
        self.assertNotIn('a', hints.flex_classes)
        self.assertEqual(base, 'http://x/assets/')

    @patch('throwback_proxy.server.http_get_cached')
    def test_error_pages_and_non_css_are_not_scanned(self, fetch):
        for result in ((404, {'Content-Type': 'text/css'}, b'.a{display:flex}'),
                       (200, {'Content-Type': 'text/html'}, b'.a{display:flex}')):
            fetch.return_value = result
            _, hints = _page_css_hints('<link rel=stylesheet href=missing.css>',
                                      'http://x/', None)
            self.assertFalse(hints.flex_classes)

    def test_internal_asset_url_uses_server_not_client_ip(self):
        handler = Handler.__new__(Handler)
        handler.connection = Mock()
        handler.connection.getsockname.return_value = ('192.0.2.10', 8080)
        handler.server = Mock(server_address=('0.0.0.0', 8080))
        handler.client_address = ('192.0.2.20', 12345)
        self.assertEqual(handler._abs_base(), 'http://192.0.2.10:8080')


class FetchAuditTests(unittest.TestCase):
    @patch('throwback_proxy.fetch._opener')
    @patch('throwback_proxy.fetch.time.monotonic', return_value=2)
    def test_error_response_uses_deadline_and_is_closed(self, clock, opener):
        fp = Mock()
        error = HTTPError('http://x/', 404, 'missing', {}, fp)
        opener.open.side_effect = error
        self.assertEqual(_get_once('http://x/', 1, 1), (None, None, None))
        fp.read.assert_not_called()
        fp.close.assert_called_once()

    @patch('throwback_proxy.fetch._get_once')
    @patch('throwback_proxy.fetch.time.monotonic', side_effect=[0, 1, 11])
    def test_redirect_does_not_restart_deadline(self, clock, once):
        once.return_value = (301, {'Location': 'https://x/'}, b'')
        self.assertEqual(http_get('http://x/', timeout=10), (None, None, None))
        once.assert_called_once_with('http://x/', 9, 10)

    def test_encoded_paths_and_fragments_preserved(self):
        url = 'http://x/a%2Fb%3Fc%23d%20e?q=1#part'
        self.assertEqual(from_proxy(to_proxy(url)), url)


if __name__ == '__main__':
    unittest.main()
