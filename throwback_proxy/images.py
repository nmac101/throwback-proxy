"""Image format conversions for IE6 (webp -> PNG via Pillow, svg -> PNG via
ImageMagick).  Both dependencies are optional; the functions become None
when the tool is missing."""

import io
import shutil
import subprocess


try:  # optional: webp -> png conversion
    from PIL import Image

    def _webp_to_png(data):
        img = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
except ImportError:  # pragma: no cover
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
        cmd += ["png:-"]
        r = subprocess.run(
            cmd,
            input=data, capture_output=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except (OSError, subprocess.SubprocessError):
        pass
