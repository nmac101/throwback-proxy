"""IE6 background images must be reusable between repaints."""

import json
import shutil
import subprocess
import unittest

from throwback_proxy.html import IE6_SHIM, transform_html
from throwback_proxy.server import _image_cache_headers


class ImageCacheTests(unittest.TestCase):
    def test_background_cache_enabled_before_stylesheets(self):
        html = transform_html('<html><head><link rel="stylesheet" href="/skin.css">'
                              '</head><body></body></html>', 'http://example.org/')
        self.assertLess(html.index("execCommand('BackgroundImageCache'"), html.index('<link'))

    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for shim tests')
    def test_cache_command_runs_once_and_unsupported_command_is_harmless(self):
        script = r'''
var vm=require('vm'),assert=require('assert');
function run(fail){
    var calls=[],context={window:{},document:{execCommand:function(){
        calls.push(Array.from(arguments));if(fail){throw new Error('Unsupported command');}
    }}};
    vm.runInNewContext(SOURCE,context);
    assert.deepStrictEqual(calls,[['BackgroundImageCache',false,true]]);
    assert.strictEqual(typeof context.window.__tbInitSizing,'function');
}
run(false);run(true);
'''
        result = subprocess.run(['node', '-'], text=True, capture_output=True, timeout=5,
                                input='var SOURCE=' + json.dumps(IE6_SHIM[8:-9]) + ';\n' + script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_origin_cache_restrictions_are_preserved(self):
        for policy in ('no-store', 'private, no-cache', 'max-age=0, must-revalidate',
                       'public, max-age=86400'):
            with self.subTest(policy=policy):
                result = _image_cache_headers({'Cache-Control': policy}, b'image')
                self.assertEqual(result['Cache-Control'], policy)
        expiry = 'Wed, 28 Oct 2026 00:00:00 GMT'
        self.assertNotIn('Cache-Control', _image_cache_headers({'Expires': expiry}, b'image'))
        self.assertEqual(_image_cache_headers({'Pragma': 'no-cache'}, b'image')['Cache-Control'],
                         'no-cache')

    def test_validator_tracks_delivered_bytes(self):
        source = {'ETag': '"svg"', 'Last-Modified': 'Tue, 30 Jun 2026 12:29:04 GMT'}
        a = _image_cache_headers(source, b'png-size-20')
        b = _image_cache_headers(source, b'png-size-40')
        self.assertNotEqual(a['ETag'], source['ETag'])
        self.assertNotEqual(a['ETag'], b['ETag'])
        self.assertEqual(a, _image_cache_headers(source, b'png-size-20'))
