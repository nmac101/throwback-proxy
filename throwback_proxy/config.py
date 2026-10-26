"""Global configuration constants for Throwback Proxy."""

HOST = "0.0.0.0"
DEFAULT_PORT = 8080
UPSTREAM_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"
)

# ---------------------------------------------------------------------------
# Ad / tracking domains (matched as host suffixes)
# ---------------------------------------------------------------------------
AD_DOMAINS = {
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.com",
    "google-analytics.com",
    "googletagmanager.com",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
    "criteo.com",
    "criteo.net",
    "pubmatic.com",
    "rubiconproject.com",
    "indexww.com",
    "moatads.com",
    "doubleverify.com",
    "adsafeprotected.com",
    "amazon-adsystem.com",
    "scorecardresearch.com",
    "quantserve.com",
    "adcolony.com",
    "mopub.com",
    "openx.net",
    "serving-sys.com",
}

TRANSPARENT_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c"
    "00000000010001000002024401003b"
)

XHTML_STRICT_DOCTYPE = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
    '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">'
)

# ---------------------------------------------------------------------------
# IE6 31-stylesheet-limit fix (Amphiluke, fix-ie-css-limit-standalone.js)
# Served from an internal path and injected into every proxied page's <head>
# after the <link> tags; consolidates excess stylesheets into @import groups.
# ---------------------------------------------------------------------------
FIX_IE_CSS_LIMIT_PATH = "/__throwback__/fix-ie-css-limit.js"

FIX_IE_CSS_LIMIT_JS = """/*!\n * fix-ie-css-limit.js by Amphiluke, 2012-2016 (c)\n * Script to fix IE limitation on style sheet number (31 as a maximum)\n * [standalone version: no jQuery required]\n *\n * https://github.com/Amphiluke/pure-js/tree/master/src/fix-ie-css-limit\n * http://amphiluke.github.io/pure-js/fix-ie-css-limit/\n *\n * Attach the script in the HEAD section of the document after all the LINK tags\n */\n\n/*@cc_on\n\n(function () {\n\n\t@if (@_jscript_version >= 10) return; @end\n\tif (document.styleSheets.length < (window.cssFixCountIE || 31)) return;\n\n\tvar links,\n\t\tpaths = [],\n\t\timportsPerStyle = 15, // a number of @import directives per one CSS file\n\t\ttmpA = document.createElement("a"),\n\t\tsheet, i, len, media, href, remain, allLinks;\n\n\tif (typeof document.querySelectorAll == "function") { // IE 9+\n\t\tlinks = Array.prototype.slice.call(document.querySelectorAll("link[rel='stylesheet']"));\n\t} else { // IE <9\n\t\tlinks = [];\n\t\tallLinks = document.getElementsByTagName("link");\n\t\tfor (i = 0, len = allLinks.length; i < len; i++) {\n\t\t\tif (allLinks[i].rel.toLowerCase() == "stylesheet") {\n\t\t\t\tlinks.push(allLinks[i]);\n\t\t\t}\n\t\t}\n\t}\n\tlen = links.length;\n\tfor (i = 0; i < len; i++) {\n\t\thref = links[i].href || "";\n\t\tmedia = links[i].media || "";\n\t\tif (href && media.indexOf("print") == -1) {\n\t\t\t // workaround for obtaining an absolute URL (independent of where the @import directive will be placed)\n\t\t\ttmpA.href = href;\n\t\t\tpaths.push(tmpA.href);\n\t\t}\n\t}\n\tremain = Math.ceil(paths.length / importsPerStyle);\n\tfor (i = 0; i < len; i++) {\n\t\tif (i < remain) {\n\t\t\ttry {\n\t\t\t\tsheet = links[i].styleSheet;\n\t\t\t\tif (sheet && sheet.cssText != "") {\n\t\t\t\t\tsheet.cssText = "";\n\t\t\t\t}\n\t\t\t} catch (e) {\n\t\t\t\t// exceptions may be rarely thrown, e.g. when a stylesheet uses\n\t\t\t\t// non-typical referencing method (like data:uri)\n\t\t\t}\n\t\t} else {\n\t\t\tlinks[i].parentNode.removeChild(links[i]);\n\t\t}\n\t}\n\tlinks.splice(remain);\n\tfor (i = 0, len = paths.length; i < len; i++) {\n\t\tlinks[Math.floor(i / importsPerStyle)].styleSheet.addImport(paths[i]);\n\t}\n\n})();\n\n@*/"""
