"""Viewport scrolling regressions for MediaWiki's full-height body."""

import json
import shutil
import subprocess
import unittest

from throwback_proxy.html import transform_html
from throwback_proxy.metrics import VIEWPORT_SCROLL_JS


class ViewportScrollingTests(unittest.TestCase):
    def test_scroll_fix_is_present_before_page_styles(self):
        html = transform_html(
            '<html><head><style>html,body{height:100%}'
            'body{overflow-y:scroll}</style></head><body>Article</body></html>',
            'https://wiki.wii-linux.org/')
        self.assertIn(VIEWPORT_SCROLL_JS, html)
        self.assertLess(html.index(VIEWPORT_SCROLL_JS), html.index('<style>'))

    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for runtime helper tests')
    def test_body_scrolls_with_viewport_and_explicit_root_is_preserved(self):
        script = r'''
var assert=require('assert'),vm=require('vm');
function page(rootOverflow,bodyOverflow,mode){
    var loaded,root={currentStyle:Object.assign({display:'block'},rootOverflow),
        style:{},scrollTop:20,scrollLeft:5};
    var body={currentStyle:Object.assign({display:'block'},bodyOverflow),
        style:{},scrollTop:80,scrollLeft:10};
    var context={document:{compatMode:mode||'CSS1Compat',documentElement:root,body:body},
        window:{attachEvent:function(event,fn){assert.strictEqual(event,'onload');loaded=fn;}}};
    vm.runInNewContext(SOURCE,context);
    assert.deepStrictEqual(root.style,{}); // Wait for all stylesheets.
    loaded();return {root:root,body:body};
}
var p=page({overflow:'visible'},{overflowX:'auto',overflowY:'scroll'});
assert.deepStrictEqual(p.root.style,{overflowX:'auto',overflowY:'scroll'});
assert.deepStrictEqual(p.body.style,{overflowX:'visible',overflowY:'visible'});
assert.strictEqual(p.root.scrollTop,100);assert.strictEqual(p.root.scrollLeft,15);
p=page({overflow:'visible'},{overflowY:'scroll',height:'100%'});
assert.strictEqual(p.body.style.height,'auto');
// Preserve pages that explicitly clip on html.
p=page({overflow:'hidden'},{overflow:'auto'});
assert.deepStrictEqual(p.root.style,{});assert.deepStrictEqual(p.body.style,{});
// IE6's default root overflow is scroll/auto: must not block the fix.
p=page({overflowX:'visible',overflowY:'scroll'},{overflow:'auto'});
assert.deepStrictEqual(p.root.style,{overflowX:'auto',overflowY:'auto'});
p=page({overflow:'visible'},{overflow:'visible'});
assert.deepStrictEqual(p.root.style,{});assert.deepStrictEqual(p.body.style,{});
p=page({overflow:'visible'},{overflow:'scroll'},'BackCompat');
assert.deepStrictEqual(p.root.style,{});assert.deepStrictEqual(p.body.style,{});
p=page({overflow:'visible'},{overflow:'hidden'});
assert.deepStrictEqual(p.root.style,{overflowX:'hidden',overflowY:'hidden'});
// Use final computed styles, not the state when the head script ran.
var loaded,root={currentStyle:{overflow:'visible'},style:{}},body={
    currentStyle:{overflow:'scroll'},style:{}};
vm.runInNewContext(SOURCE,{document:{compatMode:'CSS1Compat',documentElement:root,body:body},
    window:{attachEvent:function(event,fn){loaded=fn;}}});
root.currentStyle.overflow='hidden';loaded();
assert.deepStrictEqual(root.style,{});assert.deepStrictEqual(body.style,{});
'''
        result = subprocess.run(['node', '-'],
                                input='var SOURCE=' + json.dumps(VIEWPORT_SCROLL_JS) + ';\n' + script,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
