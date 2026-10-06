"""Image normalisation shared by the parsers."""
from __future__ import annotations

import io
import logging

from . import soffice
from .model import Limits

log = logging.getLogger("attach.images")

_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
         "webp": "image/webp", "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff"}


def normalise(data: bytes, ext_or_mime: str = "") -> tuple[bytes, str, int, int] | None:
    """Return (bytes, mime, width, height) for an image a vision model can read, or
    None when it cannot be decoded. Vector formats Office likes to embed (EMF/WMF)
    are rasterised through LibreOffice when it is installed."""
    hint = ext_or_mime.lower().split("/")[-1].lstrip(".")
    if hint in ("emf", "wmf", "x-emf", "x-wmf") or data[:4] in (b"\x01\x00\x00\x00", b"\xd7\xcd\xc6\x9a"):
        ext = "wmf" if "wmf" in hint else "emf"
        converted = soffice.convert(data, ext, "png")
        if not converted:
            return None
        data, hint = converted, "png"
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            w, h = img.size
            fmt = (img.format or "").lower()
            if fmt in ("png", "jpeg", "gif", "webp") and hint in ("", "x-png", fmt, "jpg"):
                return data, _MIME.get(fmt, "image/png"), w, h
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue(), "image/png", w, h
    except Exception as err:  # noqa: BLE001 - truncated / unsupported image
        log.info("image not decodable (%s)", err)
        return None


def worth_describing(w: int, h: int, size: int, limits: Limits) -> bool:
    """Filter icons, bullets, rule lines and spacer images that carry no information."""
    if min(w, h) < limits.min_figure_edge or w * h < limits.min_figure_area or size < limits.min_figure_bytes:
        return False
    ratio = max(w, h) / max(1, min(w, h))
    return ratio <= 14
