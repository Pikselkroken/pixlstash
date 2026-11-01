"""Read the first worksheet of an uploaded .xlsx with the standard library.

Used for the tag-group import (issue #2469), where the file is untrusted input, so
the reader is deliberately narrow:

* Only ``xl/worksheets/sheet1.xml`` and, when the book has one,
  ``xl/sharedStrings.xml`` are opened. Nothing else in the archive is read.
* Every member is streamed with a hard cap on its uncompressed size, measured as
  the bytes actually read, not the size the zip header claims. The archive may
  hold at most :data:`MAX_MEMBERS` entries.
* Each member is scanned for a DTD (``<!DOCTYPE`` / ``<!ENTITY``) before the
  parser sees the bytes that would define an entity. A DTD in any position is
  refused, so no entity is ever expanded.
* Nothing is extracted to disk.
* Completed rows and shared strings are detached from their parent as they are
  yielded, so memory stays flat on a sheet of hundreds of thousands of rows.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Iterator, Optional

MAX_MEMBERS = 256
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_COLUMNS = 16384

SHEET_MEMBER = "xl/worksheets/sheet1.xml"
SHARED_STRINGS_MEMBER = "xl/sharedStrings.xml"

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DTD_MARKERS = (b"<!DOCTYPE", b"<!ENTITY")
_TAIL = max(len(m) for m in _DTD_MARKERS) - 1
_CHUNK = 1 << 20
_COLUMN_RE = re.compile(r"^([A-Z]+)")


class XlsxError(ValueError):
    """The upload is not a readable workbook, or breaks one of the limits above."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _streamed(zf: zipfile.ZipFile, name: str) -> Iterator[bytes]:
    """Yield a member's bytes in chunks, refusing a DTD or an over-size member."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise XlsxError(f"the workbook has no {name}") from None
    if info.file_size > MAX_MEMBER_BYTES:
        raise XlsxError(f"{name} is larger than the reader allows")
    total = 0
    tail = b""
    with zf.open(info) as stream:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                return
            total += len(chunk)
            if total > MAX_MEMBER_BYTES:
                raise XlsxError(f"{name} is larger than the reader allows")
            window = tail + chunk
            if any(marker in window for marker in _DTD_MARKERS):
                raise XlsxError(f"{name} declares a DTD, which is refused")
            tail = window[-_TAIL:]
            yield chunk


def _completed(zf: zipfile.ZipFile, name: str, want: str) -> Iterator[ET.Element]:
    """Yield each element with local tag *want*, detached from its parent once used."""
    parser = ET.XMLPullParser(events=("start", "end"))
    stack: list[ET.Element] = []

    def _drain(events):
        for event, elem in events:
            if event == "start":
                stack.append(elem)
                continue
            stack.pop()
            if _local(elem.tag) != want:
                continue
            yield elem
            # The parser builds a whole chunk's tree before its events are
            # drained, so this element is rarely the parent's last child. Rows
            # complete in document order, so it is almost always the first one.
            parent = stack[-1] if stack else None
            if parent is not None:
                parent.remove(elem)

    try:
        for chunk in _streamed(zf, name):
            parser.feed(chunk)
            yield from _drain(parser.read_events())
        parser.close()
    except ET.ParseError as exc:
        raise XlsxError(f"{name} is not well-formed XML: {exc}") from None
    yield from _drain(parser.read_events())


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if SHARED_STRINGS_MEMBER not in zf.namelist():
        return []
    strings: list[str] = []
    for si in _completed(zf, SHARED_STRINGS_MEMBER, "si"):
        strings.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    return strings


def _column_index(ref: str) -> int:
    match = _COLUMN_RE.match(ref or "")
    if match is None:
        raise XlsxError(f"a cell reference {ref!r} has no column letters")
    number = 0
    for letter in match.group(1):
        number = number * 26 + ord(letter) - 64
    if number > MAX_COLUMNS:
        raise XlsxError(f"a cell in column {match.group(1)} is past the reader's limit")
    return number - 1


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
    value: Optional[ET.Element] = None
    for child in cell:
        if _local(child.tag) == "v":
            value = child
            break
    if value is None or value.text is None:
        return ""
    if kind == "s":
        index = int(value.text)
        if not 0 <= index < len(shared):
            raise XlsxError(f"a cell names shared string {index}, which does not exist")
        return shared[index]
    return value.text


def _row_values(row: ET.Element, shared: list[str]) -> list[str]:
    cells: dict[int, str] = {}
    for cell in row:
        if _local(cell.tag) != "c":
            continue
        cells[_column_index(cell.get("r", ""))] = _cell_text(cell, shared)
    if not cells:
        return []
    width = max(cells) + 1
    return [cells.get(i, "") for i in range(width)]


def iter_first_sheet_rows(data: bytes) -> Iterator[list[str]]:
    """Yield the first sheet's rows as lists of text, empty rows skipped.

    Args:
        data: The uploaded file's bytes.

    Raises:
        XlsxError: The bytes are not a zip workbook, a member is missing, a DTD
            is present, or a size or structure limit is exceeded.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XlsxError(f"not a readable .xlsx workbook: {exc}") from None
    with zf:
        if len(zf.infolist()) > MAX_MEMBERS:
            raise XlsxError("the workbook holds too many parts")
        shared = _shared_strings(zf)
        for row in _completed(zf, SHEET_MEMBER, "row"):
            values = _row_values(row, shared)
            if values:
                yield values
