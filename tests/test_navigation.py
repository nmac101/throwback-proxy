"""Regressions from the Wii-Linux wiki's Vector legacy navigation bar."""

import re
import json
import shutil
import subprocess
import unittest

from throwback_proxy.css import transform_css
from throwback_proxy.html import transform_html
from throwback_proxy.metrics import BOX_METRICS_JS, inject_box_sizing
from throwback_proxy.layout import VP_SCRIPT
from throwback_proxy.selectors import apply_synthetic_classes


class NavigationTests(unittest.TestCase):
    def css(self, source):
        return transform_css(source, 'https://wiki.wii-linux.org/')

    def test_floated_navigation_margin_is_not_doubled(self):
        out = self.css('#left-navigation{float:left;margin-left:10em;'
                       'margin-top:2.5em;margin-bottom:-2.5em}'
                       '#left-navigation{margin-left:11em}')
        self.assertIn('display: inline', out)
        self.assertIn('margin-left: 11em', out)
        self.assertNotIn('display: inline', self.css(
            '.hidden{float:right;margin-right:1em;display:none}'))

    def test_tab_height_includes_padding(self):
        out = self.css('.tabs li a{box-sizing:border-box;display:block;'
                       'float:left;height:3.07692308em;padding-top:1.25em;'
                       'padding-left:8px;padding-right:8px;font-size:.8125em}')
        self.assertNotIn("expression(", out)
        self.assertIn('padding-top: 1.25em', out)
        self.assertIn('height: 3.07692308em', out)

    def test_search_input_border_box_and_icon_dimensions(self):
        out = self.css('''
            .input {width:100%;height:2.15384615em;box-sizing:border-box;
                    border:1px solid #a2a9b1;padding:5px 2.15384615em 5px .4em}
            .searchButton {position:absolute;top:1px;bottom:1px;right:1px;
                    min-width:28px;width:2.15384615em;border:0;padding:0;
                    font-size:.8125em;text-indent:-99999px;overflow:hidden}
        ''')
        self.assertNotIn("expression(", out)
        self.assertIn('font-size: 0', out)

    def test_explicit_height_and_content_box_remain_unchanged(self):
        out = self.css('.a{position:absolute;top:1px;bottom:1px;height:20px;'
                       'box-sizing:content-box;width:30px;padding:5px}')
        self.assertNotIn('__tb', out)
        self.assertIn('height: 20px', out)

    def test_search_action_value_and_icon_selector_survive(self):
        source = ('<html><head><link rel="stylesheet" href="/skin.css"></head>'
                  '<body><form action="/index.php"><input name="search" type="search">'
                  '<input class="searchButton" name="go" type="submit" value="Go">'
                  '</form></body></html>')
        synth = {}
        css = transform_css(".searchButton[name='go']{background:no-repeat center / "
                            "1.23076923em url(/search.svg)}",
                            'https://wiki.wii-linux.org/', synth=synth)
        out = transform_html(apply_synthetic_classes(source, synth),
                             'https://wiki.wii-linux.org/', 'http://proxy:8080')
        token = re.search(r'\.((?:ie6x)[a-z0-9]+)', css)[1]
        self.assertIn('searchButton ' + token, out)
        self.assertIn('name="go" type="submit" value="Go"', out)
        self.assertIn('action="http://wiki.wii-linux.org/index.php"', out)
        self.assertLess(out.index('window.__tbInitSizing='), out.index('<link'))

    def test_sizing_targets_are_scoped_and_respect_content_box_resets(self):
        synth, rules = {}, []
        css = transform_css('.tabs a{box-sizing:border-box;height:3em}'
                            '.tabs a.small{box-sizing:content-box}'
                            '.tabs a:hover{box-sizing:border-box}',
                            'https://example.org/', synth=synth, box_rules=rules)
        html = apply_synthetic_classes('<div class="tabs"><a>A</a>'
                                       '<a class="small">B</a></div><a>C</a>', synth)
        self.assertNotIn('expression(', css)
        self.assertEqual(len(rules), 2)
        self.assertIn('<a>C</a>', html)
        self.assertIn(rules[0][0], html)
        self.assertIn(rules[1][0], html)
        out = inject_box_sizing(html, rules)
        self.assertIn('window.__tbInitSizing(', out)
        self.assertLess(out.index('"box",true'), out.index('"box",false'))

    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for runtime helper tests')
    def test_sizing_is_bounded_despite_resize_events_from_style_writes(self):
        script = r'''
var assert=require('assert'),handlers={},timers={},next=0,writes=0,all=[];
var window={attachEvent:function(event,fn){(handlers[event]||(handlers[event]=[])).push(fn);},
    setTimeout:function(fn){timers[++next]=fn;return next;},
    clearTimeout:function(id){delete timers[id];}};
function fire(event){(handlers[event]||[]).slice().forEach(function(fn){fn();});}
function flush(){var count=0;while(Object.keys(timers).length){
    assert.ok(++count<=3,'Sizing must not reschedule itself');
    var id=Object.keys(timers)[0],fn=timers[id];delete timers[id];fn();}}
function element(font,parent,cls,values){
    var e={className:cls,parentNode:parent,offsetParent:parent,style:{},
        runtimeStyle:{left:'9px',fontSize:''},currentStyle:{fontSize:font+'px',
        left:'7px',position:'static',display:'block',borderTopStyle:'none',
        borderBottomStyle:'none',borderLeftStyle:'none',borderRightStyle:'none'}};
    Object.assign(e.currentStyle,values||{});
    ['left','width','height'].forEach(function(key){var value=key=='left'?'7px':'';
        Object.defineProperty(e.style,key,{get:function(){return value;},set:function(v){
            value=v;writes++;fire('onresize');}});});
    Object.defineProperty(e.style,'pixelLeft',{get:function(){
        var f=e.runtimeStyle.fontSize,actual=font;
        if(f){actual=parseFloat(f)*(f.endsWith('em')?parseFloat(parent.currentStyle.fontSize):1);}
        return Math.round(parseFloat(e.style.left)*(e.style.left.endsWith('em')?actual:1));
    }});
    return e;
}
var root=element(16,null,'',{});root.clientWidth=432;root.clientHeight=28;
root.currentStyle.paddingLeft='16px';root.currentStyle.paddingRight='16px';
var document={documentElement:root,getElementsByTagName:function(){return all;}};
var tab=element(13,root,'tab',{height:'3.07692308em',paddingTop:'1.25em'});
var input=element(13,root,'input',{width:'100%',height:'2.15384615em',paddingTop:'5px',
    paddingBottom:'5px',paddingLeft:'.4em',paddingRight:'2.15384615em',borderTopStyle:'solid',
    borderBottomStyle:'solid',borderLeftStyle:'solid',borderRightStyle:'solid',
    borderTopWidth:'1px',borderBottomWidth:'1px',borderLeftWidth:'1px',borderRightWidth:'1px'});
var button=element(0,root,'button',{position:'absolute',width:'2.15384615em',height:'auto',top:'1px',bottom:'1px'});
var reset=element(13,root,'tab reset',{height:'3em',paddingTop:'1em'});
all=[tab,input,button,reset];
'''+BOX_METRICS_JS+r'''
window.__tbInitSizing([['tab','box',true],['input','box',true],['button','font','.8125em'],
    ['button','stretchheight',true],['reset','box',false]]);
assert.strictEqual(writes,0);assert.deepStrictEqual(Object.keys(handlers),['onload']);
fire('onload');fire('onload');assert.strictEqual(Object.keys(timers).length,1);flush();
assert.strictEqual(tab.style.height,'24px');assert.strictEqual(input.style.height,'16px');
assert.strictEqual(input.style.width,'365px');assert.strictEqual(button.style.width,'28px');
assert.strictEqual(button.style.height,'26px');assert.strictEqual(reset.style.height,'');
assert.strictEqual(tab.style.left,'7px');assert.strictEqual(tab.runtimeStyle.left,'9px');
assert.strictEqual(button.runtimeStyle.fontSize,'');
var before=writes;
for(var i=0;i<100;i++){fire('onresize');fire('onscroll');fire('onmousemove');}
assert.strictEqual(Object.keys(timers).length,0);assert.strictEqual(writes,before);
root.clientWidth=532;fire('onresize');root.clientWidth=632;fire('onresize');
assert.strictEqual(Object.keys(timers).length,1);flush();
assert.strictEqual(input.style.width,'565px');assert.strictEqual(Object.keys(timers).length,0);
// A viewport-unit handler owns its dimensions; the box pass must not
// overwrite them or trigger recursive resize events in that handler.
document.body=root;document.getElementById=function(){return input;};
root.clientHeight=800;
eval(VIEWPORT_SCRIPT);fire('onload');flush();
assert.strictEqual(input.style.height,'790px');assert.strictEqual(input._tbViewportheight,true);
before=writes;fire('onresize');flush();assert.strictEqual(writes,before);
root.clientHeight=900;fire('onresize');flush();
assert.strictEqual(input.style.height,'890px');
'''
        viewport = (VP_SCRIPT % {'els': '{id:"input",p:"height",f:1}'})
        script = 'var VIEWPORT_SCRIPT=' + json.dumps(viewport[8:-9]) + ';\n' + script
        result = subprocess.run(['node', '-'], input=script, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

