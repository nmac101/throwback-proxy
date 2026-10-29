import unittest

from throwback_proxy.css import _resolve_media
from throwback_proxy.layout import _multicolumn_layout


class ColumnLayoutTests(unittest.TestCase):
    def test_archwiki_nested_rule_keeps_definition_lists_in_reading_order(self):
        css = _resolve_media('''@media screen and (min-width:721px) {
          div.archwiki-main-page-columns {column-count:2;
            & > * {display:inline-block;width:100%}}
        }''')
        items = ['<dl><dt>Item %d</dt><dd>Description</dd></dl>' % i
                 for i in range(6)]
        html = '<div class="archwiki-main-page-columns">' + ''.join(items) + '</div>'
        out = _multicolumn_layout(html, css)
        self.assertEqual(out.count('<td '), 2)
        self.assertIn(''.join(items[:3]) + '</td>', out)
        self.assertIn(''.join(items[3:]) + '</td>', out)
        self.assertEqual(out.count('<dl>'), 6)

    def test_reset_and_scoped_selectors_do_not_create_columns(self):
        html = '<div class="columns"><p>A</p><p>B</p></div>'
        for css in ('.columns{column-count:2}.columns{column-count:auto}',
                    '.sidebar .columns{column-count:2}'):
            self.assertEqual(_multicolumn_layout(html, css), html)

    def test_odd_item_count_and_nested_elements(self):
        html = '<div class="columns">' + ''.join(
            '<div><p>%d</p></div>' % i for i in range(5)) + '</div>'
        out = _multicolumn_layout(html, '.columns{column-count:2}')
        self.assertEqual(out.count('<td '), 2)
        self.assertIn('<p>2</p></div></td>', out)
        self.assertIn('<p>4</p></div></td>', out)
