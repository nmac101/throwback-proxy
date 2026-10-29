"""Font preferences must remain conditional when CSS variables are lowered."""

import re
import unittest

from throwback_proxy.css import transform_css
from throwback_proxy.selectors import apply_synthetic_classes


class FontPreferenceTests(unittest.TestCase):
    def rules(self, source):
        css = transform_css(source, 'https://example.org/')
        return {selector.strip(): body.strip()
                for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css)}

    def test_archwiki_preferences_do_not_leak_large_font(self):
        # Reduced from Vector's stylesheet: the page's html selects pref-0.
        rules = self.rules('''
            html.pref-0,.no-font-mode-scale {
                --font-size-medium:var(--font-size-small,0.875rem);
                --line-height-content:1.5714285;
            }
            html.pref-1 {--font-size-medium:1rem;--line-height-content:1.625}
            html.pref-2 {
                --font-size-medium:var(--font-size-x-large,1.25rem);
                --line-height-content:1.55;
            }
            .vector-body {
                font-size:var(--font-size-medium);
                line-height:var(--line-height-content);
                color:black;
            }
        ''')
        self.assertNotIn('font-size', rules['.vector-body'])
        for mode, size, line in ((0, 14, '1.5714285'), (1, 16, '1.625'),
                                 (2, 20, '1.55')):
            body = rules['html.pref-%d .vector-body' % mode]
            self.assertIn('font-size: %dpx' % size, body)
            self.assertIn('line-height: ' + line, body)
            self.assertNotIn('color', body)

    def test_root_default_and_indirect_variables(self):
        rules = self.rules('''
            :root {--size:1rem;--content-size:var(--size)}
            :root.large {--size:1.25rem}
            p {font-size:var(--content-size)}
        ''')
        self.assertIn('font-size: 16px', rules['p'])
        self.assertIn('font-size: 20px', rules['html.large p'])

    def test_root_target_intersects_instead_of_becoming_descendant(self):
        synth = {}
        css = transform_css('''
            :root {--size:1rem}
            html.large {--size:1.25rem}
            html.reader {font-size:var(--size)}
        ''', 'https://example.org/', synth=synth)
        self.assertNotIn('html.large html.reader', css)
        self.assertIn('font-size: 20px', css)
        matching = apply_synthetic_classes('<html class="large reader"></html>', synth)
        other = apply_synthetic_classes('<html class="reader"></html>', synth)
        self.assertIn('ie6x', matching)
        self.assertNotIn('ie6x', other)

    def test_component_local_value_and_later_declarations_are_preserved(self):
        rules = self.rules('''
            html.large {--size:1.25rem}
            .small {--size:.75rem;font-size:var(--size)}
        ''')
        self.assertIn('font-size: 12px', rules['.small'])
        self.assertNotIn('html.large .small', rules)
        css = transform_css('''
            html.large {--size:1.25rem}
            p {font-size:var(--size)}
            p {font-size:12px !important}
        ''', 'https://example.org/')
        self.assertLess(css.index('font-size: 20px'), css.index('font-size: 12px'))

    def test_literal_font_override_in_same_rule_still_wins(self):
        for override in ('font-size:12px', 'font:12px Arial'):
            rules = self.rules('html.large{--size:1.25rem}'
                               'p{font-size:var(--size);' + override + '}')
            body = rules['html.large p']
            if override.startswith('font-size:'):
                self.assertNotIn('20px', body)
                self.assertIn('font-size: 12px', body)
            else:
                self.assertTrue(body.endswith('font: 12px Arial'))
