"""Emulate selectors IE6 can't match.

IE6 reads `.a.b` as just `.b`, cannot parse `[attr=val]`, and drops any rule
using the `+` / `~` sibling combinators or :first-child/:last-child.  Instead
each such selector is rewritten to a synthetic single class (`.ie6x<hash>`)
in the CSS, and the proxy appends that class to the elements the original
selector matches (apply_synthetic_classes), found by matching the selector
against a light parse of the HTML.  Token names are a hash of the selector,
so the CSS and HTML requests agree without sharing state.

A *spec* is a chain of compounds, left to right; each compound carries the
combinator linking it to the previous one:
    {"tag", "id", "classes", "attrs", "struct", "comb"}
"""

import hashlib
import re

_ATTR_RE = re.compile(
    r"""\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]*))"""
    r"""\s*([iIsS])?)?\s*\]""")
_SIMPLE_RE = re.compile(r"#[\w-]+|\.[\w-]+|\x00\d+\x00")
_COMPOUND_RE = re.compile(
    r"^([A-Za-z][\w-]*|\*)?((?:#[\w-]+|\.[\w-]+|\x00\d+\x00)*)(.*)$", re.S)
_STRUCT_RE = re.compile(r":(first-child|last-child|only-child)\b")
_TAG_ATTR_RE = re.compile(
    r"""([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""")
_CASE_INSENSITIVE_ATTRS = {"type", "dir", "rel", "method", "lang"}
_VOID = set("area base br col embed hr img input link meta param source "
            "track wbr".split())
# an opening one of these implicitly closes an open sibling of the same name
_AUTO_CLOSE = {"li", "dt", "dd", "p", "option", "tr", "td", "th"}


def _token(spec):
    return "ie6x" + hashlib.md5(repr(spec).encode()).hexdigest()[:8]


def _parse_compound(text, masks):
    """-> (compound dict, leftover pseudo text) or None if unparseable."""
    m = _COMPOUND_RE.match(text)
    if not m:
        return None
    tag, simples, pseudo = m.groups()
    items = _SIMPLE_RE.findall(simples)
    attrs = []
    for t in items:
        if t[0] != "\x00":
            continue
        am = _ATTR_RE.fullmatch(masks[int(t.strip("\x00"))])
        if not am:
            return None
        val = next((g for g in am.group(3, 4, 5) if g is not None), "")
        attrs.append((am.group(1).lower(), am.group(2) or "", val,
                      (am.group(6) or "").lower()))
    struct = tuple(sorted(_STRUCT_RE.findall(pseudo)))
    pseudo = _STRUCT_RE.sub("", pseudo)
    return ({"tag": (tag or "*").lower(),
             "id": next((t[1:] for t in items if t[0] == "#"), ""),
             "classes": tuple(sorted(t[1:] for t in items if t[0] == ".")),
             "attrs": tuple(attrs), "struct": struct, "comb": ""}, pseudo)


def rewrite_selector(sel, synth=None):
    """Replace the selector parts IE6 can't match by synthetic classes.
    Specs are recorded in *synth* (token -> chain) when given."""
    masks = []

    def mask(m):
        masks.append(m.group(0))
        return "\x00%d\x00" % (len(masks) - 1)

    work = re.sub(r"\[[^\]]*\]", mask, sel)
    out = ",".join(_rewrite_one(p, masks, synth) for p in work.split(","))
    return re.sub(r"\x00(\d+)\x00", lambda m: masks[int(m.group(1))], out)


def _rewrite_one(sel, masks, synth):
    lead = sel[:len(sel) - len(sel.lstrip())]
    trail = sel[len(sel.rstrip()):]
    if lead or trail:
        return lead + _rewrite_one(sel.strip(), masks, synth) + trail
    parts = re.split(r"(\s*[>+~]\s*|\s+)", sel)
    comps = []  # (index in parts, compound, pseudo)
    for i in range(0, len(parts), 2):
        pc = _parse_compound(parts[i], masks)
        if pc is None:
            return sel
        comps.append((i, pc[0], pc[1]))
    combs = [p.strip() or " " for p in parts[1::2]]
    chain_mode = any(c in "+~" for c in combs) or any(
        c["struct"] for _, c, _ in comps)
    if chain_mode:
        # dynamic pseudos (:hover...) can only ride on the last compound
        if any(p for _, _, p in comps[:-1]):
            return sel
        chain = []
        for k, (_, c, _) in enumerate(comps):
            c = dict(c)
            c["comb"] = combs[k - 1] if k else ""
            chain.append(c)
        spec = tuple(tuple(sorted(c.items())) for c in chain)
        tok = _token(spec)
        if synth is not None:
            synth[tok] = chain
        return "." + tok + comps[-1][2]
    for i, c, pseudo in comps:
        if len(c["classes"]) < 2 and not c["attrs"]:
            continue
        chain = [c]
        tok = _token(tuple(sorted(c.items())))
        if synth is not None:
            synth[tok] = chain
        parts[i] = "." + tok + pseudo
    return "".join(parts)


def _compound_matches(c, el, last_child):
    if c["tag"] != "*" and c["tag"] != el["tag"]:
        return False
    attrs = el["attrs"]
    if c["id"] and attrs.get("id") != c["id"]:
        return False
    if not set(c["classes"]) <= el["classes"]:
        return False
    for s in c["struct"]:
        if s in ("first-child", "only-child") and el["prev"] != -1:
            return False
        if s in ("last-child", "only-child") and not last_child:
            return False
    for name, op, val, flag in c["attrs"]:
        if name not in attrs:
            return False
        have = attrs[name]
        if flag == "i" or (flag != "s" and name in _CASE_INSENSITIVE_ATTRS):
            have, val = have.lower(), val.lower()
        if op == "=" and have != val:
            return False
        if op == "~=" and val not in have.split():
            return False
        if op == "|=" and not (have == val or have.startswith(val + "-")):
            return False
        if op == "^=" and not (val and have.startswith(val)):
            return False
        if op == "$=" and not (val and have.endswith(val)):
            return False
        if op == "*=" and not (val and val in have):
            return False
    return True


def _parse_elements(html):
    """Light element tree: list of dicts (tag, attrs, classes, parent, prev)
    plus {open-tag offset: index} and the last child of every parent."""
    els, at, last_child = [], {}, {}
    stack = []  # indices of open elements
    for m in re.finditer(r"<(/?)([a-zA-Z][\w:-]*)\b([^>]*)>", html):
        closing, tag, rest = m.group(1), m.group(2).lower(), m.group(3)
        if closing:
            for k in range(len(stack) - 1, -1, -1):
                if els[stack[k]]["tag"] == tag:
                    del stack[k:]
                    break
            continue
        if tag in _AUTO_CLOSE and stack and els[stack[-1]]["tag"] == tag:
            stack.pop()
        attrs = {}
        for am in _TAG_ATTR_RE.finditer(rest):
            val = next((g for g in am.group(2, 3, 4) if g is not None), "")
            attrs.setdefault(am.group(1).lower(), val)
        parent = stack[-1] if stack else -1
        idx = len(els)
        els.append({"tag": tag, "attrs": attrs, "parent": parent,
                    "classes": set(attrs.get("class", "").split()),
                    "prev": last_child.get(parent, -1)})
        last_child[parent] = idx
        at[m.start()] = idx
        if tag not in _VOID and not rest.rstrip().endswith("/"):
            stack.append(idx)
    return els, at, last_child


def _chain_matches(chain, i, idx, els, last_child):
    el = els[idx]
    c = chain[i]
    if not _compound_matches(c, el, last_child.get(el["parent"]) == idx):
        return False
    if i == 0:
        return True
    comb = c["comb"]
    if comb == "+":
        return el["prev"] != -1 and _chain_matches(
            chain, i - 1, el["prev"], els, last_child)
    if comb == "~":
        p = el["prev"]
        while p != -1:
            if _chain_matches(chain, i - 1, p, els, last_child):
                return True
            p = els[p]["prev"]
        return False
    p = el["parent"]
    while p != -1:
        if _chain_matches(chain, i - 1, p, els, last_child):
            return True
        if comb == ">":
            return False
        p = els[p]["parent"]
    return False


def apply_synthetic_classes(html, synth):
    """Add each matching synthetic class to the elements of *html*."""
    if not synth:
        return html
    by_tag, by_class, by_attr, always = {}, {}, {}, []
    for tok, chain in synth.items():
        last = chain[-1]
        if last["classes"]:
            by_class.setdefault(last["classes"][0], []).append((tok, chain))
        elif last["attrs"]:
            by_attr.setdefault(last["attrs"][0][0], []).append((tok, chain))
        elif last["tag"] != "*":
            by_tag.setdefault(last["tag"], []).append((tok, chain))
        else:
            always.append((tok, chain))
    els, at, last_child = _parse_elements(html)
    add = {}
    for start, idx in at.items():
        el = els[idx]
        cands = list(always) + by_tag.get(el["tag"], [])
        for name in el["attrs"]:
            cands += by_attr.get(name, ())
        for c in el["classes"]:
            cands += by_class.get(c, ())
        toks = sorted({t for t, ch in cands
                       if _chain_matches(ch, len(ch) - 1, idx, els,
                                         last_child)})
        if toks:
            add[start] = " ".join(toks)

    def sub(m):
        extra = add.get(m.start())
        if not extra:
            return m.group(0)
        tag, rest = m.group(1), m.group(2)
        cm = re.search(r'\bclass\s*=\s*(["\'])(.*?)\1', rest, re.I)
        if cm:
            rest = (rest[:cm.start()] + 'class=%s%s %s%s' % (
                cm.group(1), cm.group(2), extra, cm.group(1))
                + rest[cm.end():])
            return "<%s%s>" % (tag, rest)
        return '<%s class="%s"%s>' % (tag, extra, rest)

    return re.sub(r"<([a-zA-Z][\w:-]*)\b([^>]*)>", sub, html)
