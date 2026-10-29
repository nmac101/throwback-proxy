"""Fully opaque text must leave IE6's alpha-filter rendering path."""

import re
import unittest

from throwback_proxy.css import transform_css
from throwback_proxy.html import transform_html


class TextFilterTests(unittest.TestCase):
    def css(self, source):
        return transform_css(source, 'https://www.wikipedia.org/')

    def test_nmac_hover_glow_preserves_color_without_filter_or_layout(self):
        out = self.css('a:hover{color:#FFFFFF;'
                       'text-shadow:0px 0px 10px rgba(255,255,255,.75)}')
        self.assertIn('a:hover', out)
        self.assertIn('color: #FFFFFF', out)
        self.assertNotIn('filter:', out)
        self.assertNotIn('zoom:', out)

    def test_shadows_do_not_filter_text_or_cancel_other_effects(self):
        for prop in ('text-shadow', 'box-shadow'):
            for value in ('0 0 10px #000000AA', '2px 3px #000',
                          'inset 0 0 20px #fff', 'none'):
                with self.subTest(prop=prop, value=value):
                    out = self.css('a{%s:%s;color:red}' % (prop, value))
                    self.assertNotIn('filter:', out)
                    self.assertNotIn('zoom:', out)
                    out = self.css('a{opacity:.5;%s:%s}' % (prop, value))
                    self.assertIn('alpha(opacity=50)', out)
                    out = self.css('a{background:linear-gradient(red,blue);'
                                   '%s:%s}' % (prop, value))
                    self.assertIn('Microsoft.Gradient', out)
                    self.assertNotIn('Shadow', out)

    def test_inline_shadow_does_not_create_filter(self):
        out = transform_html('<a href="/" style="color:red;'
                             'text-shadow:0 0 10px white">Link</a>',
                             'http://example.org/')
        self.assertIn('style="color: red"', out)

    def test_wikipedia_visible_state_clears_loading_filter(self):
        out = self.css('''
            .js-enabled .central-featured,.js-enabled .jsl10n{opacity:0}
            .jsl10n-visible .central-featured,.jsl10n-visible .jsl10n{opacity:1}
        ''')
        rules = re.findall(r'([^{}]+)\{([^{}]*)\}', out)
        self.assertIn('alpha(opacity=0)', rules[0][1])
        self.assertIn('filter: none', rules[1][1])
        self.assertNotIn('alpha(', rules[1][1])
        self.assertNotIn('zoom', rules[1][1])

    def test_partial_transparency_is_preserved(self):
        for value, percent in (('.67', 67), ('50%', 50), ('0', 0), ('-1', 0)):
            with self.subTest(value=value):
                out = self.css('a{opacity:' + value + '}')
                self.assertIn('alpha(opacity=%d)' % percent, out)
                self.assertIn('zoom: 1', out)

    def test_fully_opaque_values_and_inline_styles(self):
        for value in ('1', '100%', '2', '.999', '99.9%'):
            out = self.css('a{opacity:' + value + '}')
            self.assertIn('filter: none', out)
            self.assertNotIn('alpha(', out)
        out = transform_html('<html><head></head><body><span style="opacity:1">Text</span>'
                             '</body></html>', 'http://example.org/')
        self.assertIn('style="filter: none"', out)

    def test_duplicate_opacity_and_important_follow_cascade(self):
        self.assertIn('filter: none', self.css('a{opacity:.5;opacity:1}'))
        self.assertIn('alpha(opacity=50)', self.css('a{opacity:1;opacity:.5}'))
        self.assertIn('alpha(opacity=50) !important',
                      self.css('a{opacity:.5 !important;opacity:1}'))
        self.assertIn('filter: none !important',
                      self.css('a{opacity:1 !important;opacity:.5}'))

    def test_explicit_reset_and_other_effects(self):
        self.assertIn('filter: none', self.css('a{filter:none}'))
        out = self.css('a{opacity:1;background:linear-gradient(red,blue)}')
        self.assertIn('Microsoft.Gradient', out)
        self.assertNotIn('alpha(', out)
        self.assertNotIn('filter: none', out)
