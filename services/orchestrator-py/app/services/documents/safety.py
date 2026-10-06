"""Guards for untrusted uploads: archive bombs and hostile XML."""
from __future__ import annotations

import io
import zipfile

from lxml import etree

from .model import IngestError, Limits


def check_zip(raw: bytes, limits: Limits) -> zipfile.ZipFile:
    """Open an OOXML/ODF/VSDX container, refusing archive bombs: too many members,
    an absurd uncompressed size, or a single member that expands past the cap."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as err:
        raise IngestError("the file is not a valid Office/zip document (corrupt or wrong extension)") from err
    infos = zf.infolist()
    if len(infos) > 20_000:
        raise IngestError("the document contains too many parts to process safely")
    total = sum(i.file_size for i in infos)
    if total > limits.max_unzipped_bytes or any(i.file_size > limits.max_unzipped_bytes // 2 for i in infos):
        raise IngestError("the document expands to an unsafe size and was not processed")
    return zf


def xml_parser() -> etree.XMLParser:
    """lxml parser with entity expansion, DTD loading and network access disabled."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                           dtd_validation=False, huge_tree=False, recover=True)


def parse_xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=xml_parser())
