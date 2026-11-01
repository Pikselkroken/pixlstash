"""Read the first worksheet of an uploaded .xlsx with the standard library.

Used for the tag-group import (issue #2469), where the file is untrusted input, so
the reader is deliberately narrow:

* Only ``xl/worksheets/sheet1.xml`` and, when the book has one,
  ``xl/sharedStrings.xml`` are opened. Nothing else in the archive is read.
* The archive may hold at most :data:`MAX_MEMBERS` entries. Every member read
  draws on one budget, :data:`MAX_UNCOMPRESSED_BYTES`, counted as the bytes
  actually read plus the text of every shared string. The size a zip header
  claims is never trusted.
* Each part must be UTF-8 XML. A UTF-16 or UTF-32 part (a byte order mark, or a
  NUL byte, which XML forbids) and a part whose XML declaration names another
  encoding are refused before anything else, so the DTD scan below reads the
  same bytes the parser does.
* Each part is then scanned for a DTD (``<!DOCTYPE`` / ``<!ENTITY``) before the
  parser sees it. A DTD in any position is refused, so no entity is expanded.
* The sheet is capped at :data:`MAX_ROWS` rows and :data:`MAX_CELLS` cells, where
  a row counts every column up to its last one.
* A corrupt, encrypted or unsupported member, a non-numeric shared-string index
  and a malformed sheet all raise :class:`XlsxError`, the only error that leaves
  this module.
* Nothing is extracted to disk.

Memory: the parser holds one completed row at a time, and shared strings are held
in full within the budget. :func:`iter_first_sheet_rows` yields each row to its
caller, which decides what to keep. The caps bound what a caller can collect; they
do not make a large sheet cheap.
"""

import codecs
import io
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Iterator, Optional

MAX_MEMBERS = 256
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ROWS = 2_000_000
MAX_CELLS = 10_000_000
MAX_COLUMNS = 16384

SHEET_MEMBER = "xl/worksheets/sheet1.xml"
SHARED_STRINGS_MEMBER = "xl/sharedStrings.xml"

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DTD_MARKERS = (b"<!DOCTYPE", b"<!ENTITY")
_TAIL = max(len(m) for m in _DTD_MARKERS) - 1
_CHUNK = 1 << 20
_COLUMN_RE = re.compile(r"^([A-Z]+)")
# A UTF-32 byte order mark begins with the UTF-16 one, so these two cover both.
_UTF16_32_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_DECLARATION_RE = re.compile(
    rb"""^\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']"""
)
_READ_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    RuntimeError,
    NotImplementedError,
    EOFError,
)


class XlsxError(ValueError):
    """The upload is not a readable workbook, or breaks one of the limits above."""


class _Budget:
    """The uncompressed bytes left for one workbook, shared by every member read."""

    def __init__(self, limit: int):
        self.left = limit

    def spend(self, count: int, name: str) -> None:
        self.left -= count
        if self.left < 0:
            raise XlsxError(
                f"the workbook's parts are larger than the reader allows ({name})"
            )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check_encoding(name: str, head: bytes) -> None:
    """Refuse a part that is not UTF-8 XML, before any byte is scanned for a DTD."""
    if head.startswith(_UTF16_32_BOMS):
        raise XlsxError(f"{name} is UTF-16 or UTF-32, not UTF-8 XML, and is refused")
    body = head[len(codecs.BOM_UTF8) :] if head.startswith(codecs.BOM_UTF8) else head
    match = _DECLARATION_RE.match(body[:256])
    if match is None:
        return
    declared = match.group(1).decode("ascii")
    try:
        found = codecs.lookup(declared).name
    except LookupError:
        found = declared
    if found != "utf-8":
        raise XlsxError(
            f"{name} declares the encoding {declared!r}, not UTF-8, and is refused"
        )


def _streamed(zf: zipfile.ZipFile, name: str, budget: _Budget) -> Iterator[bytes]:
    """Yield a member's bytes in chunks, refusing a non-UTF-8, DTD-bearing or over-size part."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise XlsxError(f"the workbook has no {name}") from None
    if info.file_size > budget.left:
        raise XlsxError(f"{name} is larger than the reader allows")
    tail = b""
    try:
        with zf.open(info) as stream:
            checked = False
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    return
                if not checked:
                    _check_encoding(name, chunk)
                    checked = True
                if b"\x00" in chunk:
                    raise XlsxError(f"{name} holds a NUL byte, so it is not UTF-8 XML")
                budget.spend(len(chunk), name)
                window = tail + chunk
                if any(marker in window for marker in _DTD_MARKERS):
                    raise XlsxError(f"{name} declares a DTD, which is refused")
                tail = window[-_TAIL:]
                yield chunk
    except _READ_ERRORS as exc:
        raise XlsxError(f"{name} could not be read: {exc}") from None


def _completed(
    zf: zipfile.ZipFile, name: str, want: str, budget: _Budget
) -> Iterator[ET.Element]:
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
        for chunk in _streamed(zf, name, budget):
            parser.feed(chunk)
            yield from _drain(parser.read_events())
        parser.close()
    except ET.ParseError as exc:
        raise XlsxError(f"{name} is not well-formed XML: {exc}") from None
    yield from _drain(parser.read_events())


def _rich_text(container: ET.Element) -> str:
    """The text of a string item (``<si>``) or an inline string (``<is>``).

    Only its own ``<t>`` and the ``<t>`` of its runs (``<r>``) are the string;
    a phonetic run (``<rPh>``, furigana) carries a reading, not the text, and is
    left out.
    """
    parts: list[str] = []
    for child in container:
        name = _local(child.tag)
        if name == "t":
            parts.append(child.text or "")
        elif name == "r":
            parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
    return "".join(parts)


def _shared_strings(zf: zipfile.ZipFile, budget: _Budget) -> list[str]:
    if SHARED_STRINGS_MEMBER not in zf.namelist():
        return []
    strings: list[str] = []
    for si in _completed(zf, SHARED_STRINGS_MEMBER, "si", budget):
        text = _rich_text(si)
        budget.spend(len(text), SHARED_STRINGS_MEMBER)
        strings.append(text)
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
        inline = next((c for c in cell if _local(c.tag) == "is"), None)
        return _rich_text(inline) if inline is not None else ""
    value: Optional[ET.Element] = None
    for child in cell:
        if _local(child.tag) == "v":
            value = child
            break
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            index = int(value.text)
        except ValueError:
            raise XlsxError(
                f"a cell names shared string {value.text!r}, which is not a whole number"
            ) from None
        if not 0 <= index < len(shared):
            raise XlsxError(f"a cell names shared string {index}, which does not exist")
        return shared[index]
    return value.text


def _row_values(row: ET.Element, shared: list[str]) -> list[str]:
    cells: dict[int, str] = {}
    # ``r`` is optional in OOXML: a cell without it sits in the column after the
    # previous cell, so the position is tracked and used when ``r`` is absent.
    next_column = 0
    for cell in row:
        if _local(cell.tag) != "c":
            continue
        ref = cell.get("r")
        column = _column_index(ref) if ref else next_column
        # The positional fallback is held to the same limit as a reference.
        if column >= MAX_COLUMNS:
            raise XlsxError("a row has more cells than the reader's column limit")
        cells[column] = _cell_text(cell, shared)
        next_column = column + 1
    if not cells:
        return []
    width = max(cells) + 1
    return [cells.get(i, "") for i in range(width)]


def iter_first_sheet_rows(data: bytes) -> Iterator[list[str]]:
    """Yield the first sheet's rows as lists of text, empty rows skipped.

    Args:
        data: The uploaded file's bytes.

    Raises:
        XlsxError: The bytes are not a zip workbook, a member is missing or is not
            UTF-8 XML, a DTD is present, a member cannot be decompressed, or a size
            or structure limit is exceeded.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, EOFError, ValueError, NotImplementedError) as exc:
        raise XlsxError(f"not a readable .xlsx workbook: {exc}") from None
    budget = _Budget(MAX_UNCOMPRESSED_BYTES)
    rows_seen = 0
    cells_seen = 0
    with zf:
        if len(zf.infolist()) > MAX_MEMBERS:
            raise XlsxError("the workbook holds too many parts")
        shared = _shared_strings(zf, budget)
        for row in _completed(zf, SHEET_MEMBER, "row", budget):
            rows_seen += 1
            if rows_seen > MAX_ROWS:
                raise XlsxError(f"the sheet has more than {MAX_ROWS:,} rows")
            values = _row_values(row, shared)
            if values:
                cells_seen += len(values)
                if cells_seen > MAX_CELLS:
                    raise XlsxError(f"the sheet has more than {MAX_CELLS:,} cells")
                yield values
