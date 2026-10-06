"""Optional LibreOffice bridge.

When ``soffice`` is installed it lets the platform (a) open legacy / OpenDocument
formats (.doc .ppt .xls .odt .odp .ods) by converting them to OOXML and (b) *see*
shape-built diagrams in slides and Word pages by rendering them to PDF and then to
images. Everything degrades cleanly when it is absent: the structured text
extractors still run, and the figure is reported as not rendered.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("attach.soffice")

_TIMEOUT = 90


def available() -> bool:
    return shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def _binary() -> str:
    return shutil.which("soffice") or shutil.which("libreoffice") or "soffice"


def convert(raw: bytes, src_ext: str, target: str) -> bytes | None:
    """Convert ``raw`` (a file with extension ``src_ext``, no dot) to ``target``
    (e.g. 'pdf', 'docx', 'pptx', 'xlsx'). Returns the converted bytes, or None on any
    failure. Runs in an isolated temp dir with its own profile, no shell, a hard
    timeout."""
    if not available():
        return None
    with tempfile.TemporaryDirectory(prefix="devmind-soffice-") as tmp:
        src = Path(tmp) / f"input.{src_ext}"
        out_dir = Path(tmp) / "out"
        out_dir.mkdir()
        src.write_bytes(raw)
        profile = Path(tmp) / "profile"
        cmd = [_binary(), f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
               "--nolockcheck", "--convert-to", target, "--outdir", str(out_dir), str(src)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=_TIMEOUT,
                           env={**os.environ, "HOME": tmp})
        except (subprocess.SubprocessError, OSError) as err:
            log.warning("soffice conversion %s->%s failed: %s", src_ext, target, err)
            return None
        produced = out_dir / f"input.{target.split(':')[0]}"
        return produced.read_bytes() if produced.exists() else None
