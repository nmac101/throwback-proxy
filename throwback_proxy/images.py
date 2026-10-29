"""Image format conversions for IE6 (webp -> PNG via Pillow, svg -> PNG via
ImageMagick).  Both dependencies are optional; the functions become None
when the tool is missing."""

import io
import shutil
import subprocess


try:  # optional: Pillow handles webp/avif decoding and the PNG alpha fix
    from PIL import Image

    # Partially transparent pixels are blended onto this colour (most pages
    # are light) because IE6 can only do on/off transparency.
    _MATTE = (255, 255, 255)
    _ALPHA_CUTOFF = 128

    def _ie6_png(data, force=False):
        """Re-encode an image as an IE6-safe PNG, or None if nothing needs
        doing.  IE6 can't render 24/32-bit PNG alpha (transparent areas come
        out opaque gray/black), but 8-bit palette PNGs with a single
        transparent index work like GIF.  So any image with an alpha channel
        becomes: colours matted onto white where partially transparent,
        alpha thresholded to on/off, quantised to <=255 colours + one
        transparent index.  Opaque images are returned untouched (None)
        unless *force* (formats IE6 can't read at all, e.g. webp)."""
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            t = img.info.get("transparency")
            if (img.mode == "P" and not force and (
                    isinstance(t, int) or
                    (isinstance(t, bytes) and set(t) <= {0, 255}))):
                return None  # already 8-bit with on/off transparency
            has_alpha = (img.mode in ("RGBA", "LA", "PA")
                         or "transparency" in img.info)
            if not has_alpha and not force:
                return None
            rgba = img.convert("RGBA")
            alpha = rgba.getchannel("A")
            # blend partial alpha onto the matte colour
            flat = Image.new("RGB", rgba.size, _MATTE)
            flat.paste(rgba, mask=alpha)
            if not has_alpha:
                buf = io.BytesIO()
                flat.save(buf, "PNG")
                return buf.getvalue()
            few = flat.getcolors(256)
            pal = flat.quantize(
                colors=255, method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.NONE if few
                else Image.Dither.FLOYDSTEINBERG)
            # index 255 is never used by a 255-colour quantisation; pad the
            # palette so it exists (else the PNG writer remaps it away)
            colors = pal.getpalette()
            pal.putpalette(colors + [0] * (768 - len(colors)))
            clear = alpha.point(lambda a: 255 if a < _ALPHA_CUTOFF else 0)
            pal.paste(255, mask=clear)
            buf = io.BytesIO()
            pal.save(buf, "PNG", transparency=255)
            return buf.getvalue()
        except Exception:
            return None

    def _webp_to_png(data):
        """webp/avif -> IE6-safe PNG (None if undecodable)."""
        return _ie6_png(data, force=True)
except ImportError:  # pragma: no cover
    _ie6_png = None
    _webp_to_png = None


_IM = next((shutil.which(t) for t in ("magick", "convert") if shutil.which(t)),
           None)
def _svg_to_png(data, width=None):
    """SVG -> PNG via ImageMagick (optional).  IE6 cannot render SVG.  When
    *width* is given the PNG is rendered at that width (aspect preserved) so
    the browser shows it 1:1 -- IE6 paints the alpha of a *scaled* transparent
    PNG black, so we avoid making it scale."""
    if not _IM:
        return None
    try:
        cmd = [_IM, "-background", "transparent", "-depth", "8", "svg:-"]
        if width:
            # -resize is an operation, so it must follow the input image
            cmd += ["-resize", "%dx" % width]
        # Drop conversion-time metadata so identical icons have stable
        # downstream bytes/ETags, including when Pillow isn't installed.
        cmd += ["-strip", "png:-"]
        r = subprocess.run(
            cmd,
            input=data, capture_output=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except (OSError, subprocess.SubprocessError):
        pass
