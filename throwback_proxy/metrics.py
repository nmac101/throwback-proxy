"""Small IE6 runtime helpers for sizing and document scrolling."""

import json
import re

from .selectors import rewrite_selector, split_selectors


def record_box_rules(selector, body, synth, rules):
    """Record static targets for a load/resize pass, never CSS expressions."""
    declarations = {}
    for declaration in body.split(';'):
        prop, colon, value = declaration.partition(':')
        if not colon:
            continue
        prop = prop.strip().lower()
        important = bool(re.search(r'!\s*important\s*$', value, re.I))
        value = re.sub(r'\s*!\s*important\s*$', '', value, flags=re.I).strip()
        if prop not in declarations or important or not declarations[prop][1]:
            declarations[prop] = (value, important)
    values = {p: v for p, (v, _) in declarations.items()}
    options = {}
    if values.get('box-sizing') in ('border-box', 'content-box'):
        options['box'] = (values['box-sizing'] == 'border-box', declarations['box-sizing'][1])
    indent = re.fullmatch(r'-\s*(\d+)(?:px|em)?', values.get('text-indent', ''))
    if indent and int(indent[1]) >= 900:
        font = values.get('font-size', 'inherit')
        if not re.fullmatch(r'(?:\d*\.?\d+(?:px|em|pt|%)|inherit)', font, re.I):
            font = 'inherit'
        options['font'] = (font, declarations['text-indent'][1])
    if values.get('position') == 'absolute':
        for axis, start, end in (('height', 'top', 'bottom'), ('width', 'left', 'right')):
            if axis not in values and start in values and end in values:
                options['stretch' + axis] = (True, declarations['position'][1])
    if not options:
        return
    for part in split_selectors(selector):
        compiled = rewrite_selector(part.strip(), synth, force_static=True)
        if not re.fullmatch(r'\.ie6x[0-9a-f]+', compiled):
            continue
        token = compiled[1:]
        chain = synth[token]
        specificity = (sum(bool(c['id']) for c in chain),
                       sum(len(c['classes']) + len(c['attrs']) + len(c['struct']) for c in chain),
                       sum(c['tag'] != '*' for c in chain))
        for key, (value, important) in options.items():
            rules.append((token, key, value, (int(important),) + specificity))


def inject_box_sizing(html, rules):
    if not rules:
        return html
    # Sort by importance/specificity, retaining source order for ties. A
    # matching content-box reset can cancel an earlier border-box rule.
    ordered = [list(rule[:3]) for rule in sorted(rules, key=lambda r: r[3])]
    data = json.dumps(ordered, separators=(',', ':')).replace('<', '\\u003c')
    script = '<script>window.__tbInitSizing(' + data + ');</script>'
    close = re.search(r'</body\s*>', html, re.I)
    return html[:close.start()] + script + html[close.start():] if close else html + script

# IE6 can make a height:100% body with overflow-y:scroll a separate scroll
# container. Explicitly implement body-to-viewport overflow propagation.
# Read currentStyle after all sheets load so this respects stylesheet order,
# inline styles and root class selectors rather than guessing from CSS text.
# https://www.w3.org/TR/css-overflow-3/#overflow-propagation
VIEWPORT_SCROLL_JS = r"""
(function(){
function init(){
    var root=document.documentElement,body=document.body,r,b,x,y,top,left;
    if(document.compatMode!='CSS1Compat'||!root||!body||!root.currentStyle){return;}
    r=root.currentStyle;b=body.currentStyle;
    if(!b||r.display=='none'||b.display=='none'){return;}
    function overflow(style,axis){return style['overflow'+axis]||style.overflow||'visible';}
    // IE6 reports its root as scroll/auto by default, so only a clipping
    // root proves the body is intentionally a separate scroll container.
    if(overflow(r,'X')=='hidden'||overflow(r,'Y')=='hidden'){return;}
    x=overflow(b,'X');y=overflow(b,'Y');
    if(x=='visible'&&y=='visible'){return;}
    if(!/^(visible|auto|scroll|hidden)$/.test(x)||
       !/^(visible|auto|scroll|hidden)$/.test(y)){return;}
    top=(root.scrollTop||0)+(body.scrollTop||0);
    left=(root.scrollLeft||0)+(body.scrollLeft||0);
    root.style.overflowX=x;root.style.overflowY=y;
    body.style.overflowX='visible';body.style.overflowY='visible';
    // A body still sized to the viewport would clip what the root should scroll.
    if(/%$/.test(b.height)){body.style.height='auto';}
    root.scrollTop=top;root.scrollLeft=left;
}
if(window.attachEvent){window.attachEvent('onload',init);}
})();
"""

# currentStyle keeps em lengths unresolved. style.pixelLeft converts them
# using the element's font; runtimeStyle keeps the temporary ruler from
# moving the element. Keep this ES3-compatible (including IE6's host objects).
BOX_METRICS_JS = r"""
(function(){
function container(e){
    var c=e.currentStyle||{},p;
    if(c.position=='absolute'){return e.offsetParent||document.documentElement;}
    p=e.parentNode;
    while(p&&p.currentStyle&&p.currentStyle.display=='inline'){p=p.parentNode;}
    return p||document.documentElement;
}
function px(e,v,axis,font){
    v=String(v||'0');
    var n=parseFloat(v),p,c,size,l,r,f,result;
    if(isNaN(n)){return 0;}
    if(/%$/.test(v)){
        p=container(e);c=p.currentStyle||{};
        size=axis=='height'?p.clientHeight:p.clientWidth;
        if((e.currentStyle||{}).position!='absolute'){
            size-=axis=='height'
                ?px(p,c.paddingTop,'width')+px(p,c.paddingBottom,'width')
                :px(p,c.paddingLeft,'width')+px(p,c.paddingRight,'width');
        }
        return n*Math.max(0,size||0)/100;
    }
    if(/px$/.test(v)||n==0){return n;}
    if(!e.runtimeStyle){return n;}
    l=e.style.left;r=e.runtimeStyle.left;f=e.runtimeStyle.fontSize;
    try{
        e.runtimeStyle.left=e.currentStyle.left;
        if(font){e.runtimeStyle.fontSize=font=='inherit'
            ?(e.parentNode.currentStyle||{}).fontSize:font;}
        e.style.left=v;result=e.style.pixelLeft;
    }finally{
        e.style.left=l;e.runtimeStyle.left=r;e.runtimeStyle.fontSize=f;
    }
    return result||0;
}
function edges(e,axis,font){
    var c=e.currentStyle||{},s=axis=='height'?['Top','Bottom']:['Left','Right'];
    var total=0,i,v;
    for(i=0;i<2;i++){
        total+=px(e,c['padding'+s[i]],'width',font);
        if(c['border'+s[i]+'Style']!='none'&&c['border'+s[i]+'Style']!='hidden'){
            v=c['border'+s[i]+'Width'];
            total+=v=='thin'?1:v=='medium'?3:v=='thick'?5:px(e,v,axis,font);
        }
    }
    return total;
}
function size(e,axis,value,borderBox,font){
    return Math.max(0,px(e,value,axis,font)-(borderBox?edges(e,axis,font):0))+'px';
}
function stretch(e,axis,start,end,font){
    var p=e.offsetParent||document.documentElement;
    var c=e.currentStyle||{},s=axis=='height'?['Top','Bottom']:['Left','Right'];
    var size=axis=='height'?p.clientHeight:p.clientWidth;
    return Math.max(0,size-px(e,start,axis,font)-px(e,end,axis,font)
        -edges(e,axis,font)-px(e,c['margin'+s[0]],'width',font)
        -px(e,c['margin'+s[1]],'width',font))+'px';
}
window.__tbInitSizing=function(rules){
    if(!window.attachEvent||!document.documentElement.currentStyle){return;}
    var targets=[],timer=null,running=false,initialized=false,lastW,lastH;
    var length=/^(?:\d*\.?\d+(?:px|em|pt|%)|0)$/i;
    function viewport(){
        var d=document.documentElement;
        lastW=d.clientWidth;lastH=d.clientHeight;
    }
    function update(){
        timer=null;if(running){return;}running=true;viewport();
        try{
            for(var i=0;i<targets.length;i++){
                var t=targets[i],e=t.e,c=e.currentStyle,axis,value;
                // A measurement failure must not stop the remaining page.
                try{
                    for(var j=0;j<t.axes.length;j++){
                        var a=t.axes[j];axis=a.axis;
                        if(e['_tbViewport'+axis]){continue;}
                        value=a.stretch?stretch(e,axis,c[a.start],c[a.end],t.font)
                            :size(e,axis,a.value,t.box,t.font);
                        if(e.style[axis]!=value){e.style[axis]=value;}
                    }
                }catch(ignore){}
            }
        }finally{viewport();running=false;}
    }
    function resize(){
        // IE can fire resize while styles are being assigned. Only a real
        // viewport change may schedule work; no mouse/scroll/layout polling.
        var d=document.documentElement;
        if(running||!initialized||(d.clientWidth==lastW&&d.clientHeight==lastH)){return;}
        viewport();if(timer!==null){window.clearTimeout(timer);}
        timer=window.setTimeout(update,80);
    }
    function init(){
        if(initialized){return;}initialized=true;
        var all=document.getElementsByTagName('*'),i,j,e,c,classes,options,axes,axis,value;
        for(i=0;i<all.length;i++){
            e=all[i];c=e.currentStyle;if(!c){continue;}
            classes=' '+String(e.className||'').replace(/\s+/g,' ')+' ';options={};
            for(j=0;j<rules.length;j++){
                if(classes.indexOf(' '+rules[j][0]+' ')!=-1){options[rules[j][1]]=rules[j][2];}
            }
            axes=[];
            for(j=0;j<2;j++){
                axis=j?'height':'width';value=c[axis]||'auto';
                if(length.test(value)&&(options.box||(options.font&&/em$/i.test(value)))){
                    axes.push({axis:axis,value:value});
                }else if(options['stretch'+axis]&&value=='auto'&&c.position=='absolute'){
                    var start=j?'top':'left',end=j?'bottom':'right';
                    if(length.test(c[start])&&length.test(c[end])){
                        axes.push({axis:axis,stretch:true,start:start,end:end});
                    }
                }
            }
            if(axes.length){targets.push({e:e,axes:axes,box:!!options.box,font:options.font||''});}
        }
        viewport();
        // Run after other onload handlers (notably viewport-unit sizing).
        if(targets.length){
            timer=window.setTimeout(update,0);
            window.attachEvent('onresize',resize);
        }
    }
    window.attachEvent('onload',init);
};
})();
"""
