"""MediaWiki components with deterministic IE6 layout and keyboard access."""

import json
import re
import shutil
import subprocess
import unittest

from throwback_proxy.compat import apply_mediawiki_fallbacks, SKIP_LINK_SCRIPT


HEAD = '<html><head><meta name="generator" content="MediaWiki 1.46.2"></head><body>'
TOC = ('<div id="toc" class="toc" role="navigation" aria-labelledby="mw-toc-heading">'
       '<div class="toctitle"><h2 id="mw-toc-heading">Contents</h2></div>'
       '<ul><li><a href="#Requirements">Requirements</a></li>'
       '<li><a href="#Installation">Installation process</a></li></ul></div>')


class MediaWikiTests(unittest.TestCase):
    def test_contents_shrinks_and_following_section_clears_entire_box(self):
        out = apply_mediawiki_fallbacks(HEAD + TOC + '<h2 id="Requirements">Requirements</h2>'
                                      '</body></html>')
        self.assertIn('class="toc tb-ie6-toc"', out)
        self.assertIn('float:left;width:auto;display:block;', out)
        self.assertIn('href="#Requirements"', out)
        self.assertIn('aria-labelledby="mw-toc-heading"', out)
        clear = out.index('clear:left')
        self.assertGreater(clear, out.index('</ul></div>'))
        self.assertLess(clear, out.index('<h2 id="Requirements">'))
        self.assertNotIn('expression(', out)
        self.assertNotIn('<script>', out)  # TOC layout requires no script.

    def test_skip_links_keep_their_destinations_and_keyboard_access(self):
        source = HEAD + ('<a class="mw-jump-link extra" href="#mw-head">Jump to navigation</a>'
                         '<a class=mw-jump-link href="#searchInput">Jump to search</a>'
                         '<a href="#other">Other</a></body></html>')
        out = apply_mediawiki_fallbacks(source)
        self.assertIn('class="mw-jump-link extra tb-ie6-skip-hidden"', out)
        self.assertIn('class="mw-jump-link tb-ie6-skip-hidden"', out)
        self.assertIn('href="#mw-head">Jump to navigation</a>', out)
        self.assertIn('href="#searchInput">Jump to search</a>', out)
        self.assertIn('<a href="#other">Other</a>', out)
        self.assertNotIn('display:none', out)
        self.assertNotIn('tabindex="-1"', out)
        self.assertIn(SKIP_LINK_SCRIPT, out)

    def test_adapter_is_scoped_idempotent_and_preserves_raw_text(self):
        unrelated = '<html><head></head><body>' + TOC + '</body></html>'
        self.assertEqual(apply_mediawiki_fallbacks(unrelated), unrelated)
        script = '<script>var text=\'<div id="toc" class="toc">fake</div>\';</script>'
        source = HEAD + script + TOC + '<a class="mw-jump-link" href="#x">Jump</a></body></html>'
        once = apply_mediawiki_fallbacks(source)
        self.assertIn(script, once)
        self.assertEqual(once.count('clear:left'), 1)
        self.assertEqual(apply_mediawiki_fallbacks(once), once)

    def test_native_table_toc_is_unchanged(self):
        source = HEAD + '<table id="toc" class="toc"><tr><td>Contents</td></tr></table></body></html>'
        self.assertEqual(apply_mediawiki_fallbacks(source), source)

    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for keyboard event tests')
    def test_focus_reveals_link_and_blur_hides_it_without_replacing_handlers(self):
        script = r'''
var vm=require('vm'),assert=require('assert');
function link(cls){return {className:cls,events:{},attachEvent:function(name,fn){
    (this.events[name]||(this.events[name]=[])).push(fn);
}};}
var nav=link('mw-jump-link extra tb-ie6-skip-hidden'),search=link('mw-jump-link tb-ie6-skip-hidden');
var ordinary=link('ordinary'),old=0;nav.attachEvent('onfocus',function(){old++;});
vm.runInNewContext(SOURCE,{document:{getElementsByTagName:function(){return [nav,search,ordinary];}}});
function fire(el,event){el.events[event].forEach(function(fn){fn();});}
fire(nav,'onfocus');assert.strictEqual(old,1);
assert.ok(!nav.className.includes('tb-ie6-skip-hidden'));
assert.ok(nav.className.includes('extra'));
assert.ok(search.className.includes('tb-ie6-skip-hidden'));
fire(nav,'onblur');fire(nav,'onblur');
assert.strictEqual(nav.className.split('tb-ie6-skip-hidden').length,2);
fire(search,'onfocus');assert.ok(!search.className.includes('tb-ie6-skip-hidden'));
assert.deepStrictEqual(ordinary.events,{});
'''
        result = subprocess.run(['node', '-'], text=True, capture_output=True, timeout=5,
                                input='var SOURCE=' + json.dumps(SKIP_LINK_SCRIPT[8:-9]) + ';\n' + script)
        self.assertEqual(result.returncode, 0, result.stderr)
