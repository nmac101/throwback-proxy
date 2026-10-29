"""CSS parsing helpers: balanced-bracket scanning and @media query evaluation."""

import re

from .config import VIEWPORT_HEIGHT, VIEWPORT_WIDTH


def _balanced_paren(s, i):
    """s[i] == '(' -> index of matching ')'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(s) - 1


def _balanced_brace(s, i):
    """s[i] == '{' -> index of matching '}'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(s) - 1


def _length_px(v):
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*(px|em|rem)?\s*", v)
    if not m:
        return None
    n = float(m.group(1))
    return n * 16 if m.group(2) in ("em", "rem") else n


def _feature_true(feat):
    """Evaluate one parenthesised media feature for the assumed viewport."""
    f = feat.strip()
    if f.lower().startswith("not "):
        return not _feature_true(f[4:].strip().strip("()"))
    dims = {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT,
            "device-width": VIEWPORT_WIDTH, "device-height": VIEWPORT_HEIGHT}
    if ":" in f:
        name, _, val = f.partition(":")
        name, val = name.strip().lower(), val.strip().lower()
        m = re.fullmatch(r"(min|max)-(.+)", name)
        kind, base = (m.group(1), m.group(2)) if m else (None, name)
        base = base.replace("-webkit-", "").replace("-moz-", "")
        if base in dims:
            px = _length_px(val)
            if px is None:
                return False
            return (dims[base] >= px if kind == "min"
                    else dims[base] <= px if kind == "max"
                    else dims[base] == px)
        if base in ("resolution", "device-pixel-ratio"):
            rm = re.fullmatch(r"([\d.]+)\s*(dppx|dpi|x)?", val)
            if not rm:
                return False
            n = float(rm.group(1))
            if rm.group(2) == "dpi":
                n /= 96.0
            return (1 >= n if kind == "min" else 1 <= n if kind == "max"
                    else n == 1)
        table = {
            ("orientation", "landscape"): True,
            ("hover", "hover"): True, ("any-hover", "hover"): True,
            ("pointer", "fine"): True, ("any-pointer", "fine"): True,
            ("prefers-color-scheme", "light"): True,
            ("prefers-reduced-motion", "no-preference"): True,
            ("prefers-contrast", "no-preference"): True,
            ("prefers-reduced-transparency", "no-preference"): True,
            ("display-mode", "browser"): True,
            ("update", "fast"): True, ("scripting", "none"): True,
        }
        return table.get((name, val), False)
    # range syntax: (width >= 1120px), (400px <= width <= 800px)
    parts = re.split(r"\s*(<=|>=|<|>|=)\s*", f)
    if len(parts) >= 3:
        ops = parts[1::2]
        vals = parts[0::2]
        cmp = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
               ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
               "=": lambda a, b: a == b}
        nums = []
        for v in vals:
            nums.append(dims[v.strip().lower()] if v.strip().lower() in dims
                        else _length_px(v))
        if None in nums:
            return False
        return all(cmp[o](nums[i], nums[i + 1]) for i, o in enumerate(ops))
    return f.lower() in ("color", "hover", "any-hover", "pointer",
                         "any-pointer")  # boolean-context features


def _media_query_true(query):
    """Does a media query list (comma = or) match the assumed viewport?"""
    for q in query.split(","):
        q = q.strip().lower()
        if not q:
            return True
        neg = False
        if q.startswith("not "):
            neg, q = True, q[4:].strip()
        q = re.sub(r"^only\s+", "", q)
        mt = re.match(r"([a-z-]+)", q)
        mtype = mt.group(1) if mt and mt.group(1) not in ("and", "not") \
            else "all"
        ok = mtype in ("all", "screen")
        i = 0
        while ok and i < len(q):
            if q[i] == "(":
                j = _balanced_paren(q, i)
                ok = _feature_true(q[i + 1:j])
                i = j + 1
            else:
                i += 1
        if ok != neg:
            return True
    return False


def _resolve_media(css):
    """Evaluate @media blocks against the assumed desktop viewport: matching
    blocks are unwrapped in place, the others removed.  (IE6 knows no @media,
    and stripping them all loses the desktop layout that modern sites define
    in min-width queries.)  `@supports not (...)` fallbacks are unwrapped as
    well -- IE6 supports nothing -- and the other @supports blocks dropped."""
    pat = re.compile(r"@(media|supports)\b([^{;]*)\{", re.I)
    pos = 0
    while True:
        m = pat.search(css, pos)
        if not m:
            return css
        j = _balanced_brace(css, m.end() - 1)
        if m.group(1).lower() == "media":
            keep = _media_query_true(m.group(2))
        else:
            keep = m.group(2).strip().lower().startswith("not ")
        if keep:
            css = css[:m.start()] + css[m.end():j] + css[j + 1:]
            pos = m.start()  # re-scan the unwrapped contents
        else:
            css = css[:m.start()] + css[j + 1:]
            pos = m.start()


def _strip_at_blocks(css, names):
    pat = re.compile(r"@(?:" + "|".join(names) + r")\b[^{]*\{", re.I)
    while True:
        m = pat.search(css)
        if not m:
            return css
        j = _balanced_brace(css, m.end() - 1)
        css = css[: m.start()] + " " + css[j + 1 :]
