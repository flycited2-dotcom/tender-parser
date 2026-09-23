"""Best-effort text extraction from Gmail attachments without executing them.

Each attachment is handled independently. A corrupt, unsupported, or scanned
file yields empty text (and metadata in ``extract_attachments``) so the rest of
the message can still be processed. OCR is intentionally outside this module.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


MAX_ATTACHMENT_BYTES = 25_000_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100_000_000
MAX_TEXT_CHARS = 100_000
SUPPORTED_EXTENSIONS = {
    ".pdf", ".xls", ".xlsx", ".doc", ".docx",
    ".txt", ".csv", ".xml", ".html", ".htm",
}


def extract_attachment_text(filename: str, content: bytes) -> str:
    """Return extracted text, or an empty string on *any* extraction failure.

    The 25 MB input and 100,000 character output limits keep mailbox backfill
    bounded. A caller needing a whole large price list should archive the
    original attachment separately and parse it with a dedicated price tool.
    """

    try:
        if not isinstance(content, bytes) or len(content) > MAX_ATTACHMENT_BYTES:
            return ""
        return _extract_text(filename, content)[:MAX_TEXT_CHARS]
    except Exception:
        return ""


def extract_attachments(
    message: Mapping[str, Any],
    fetch_attachment: Callable[[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Extract every MIME attachment from a raw Gmail ``full`` message.

    ``fetch_attachment`` is normally ``GmailCollector.get_attachment``.
    Results retain filename, MIME type, part/attachment IDs, byte size, text,
    status, and a short error type. Raw bytes are never returned or logged.
    """

    message_id = str(message.get("id") or "")
    result: list[dict[str, Any]] = []
    for part in _iter_parts(message.get("payload") or {}):
        headers = part.get("headers") or []
        disposition = next(
            (str(h.get("value") or "") for h in headers
             if str(h.get("name") or "").lower() == "content-disposition"),
            "",
        )
        filename = str(part.get("filename") or "").strip()
        if not filename and "attachment" not in disposition.lower():
            continue
        mime_type = str(part.get("mimeType") or "application/octet-stream")
        if not filename:
            ext = mimetypes.guess_extension(mime_type) or ""
            filename = f"attachment-{len(result) + 1}{ext}"
        body = part.get("body") or {}
        attachment_id = str(body.get("attachmentId") or "")
        row: dict[str, Any] = {
            "filename": filename,
            "mime_type": mime_type,
            "part_id": str(part.get("partId") or ""),
            "attachment_id": attachment_id,
            "size": body.get("size"),
            "text": "",
            "status": "empty",
            "error": "",
        }
        result.append(row)
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            row["status"] = "unsupported"
            continue
        if isinstance(row["size"], int) and row["size"] > MAX_ATTACHMENT_BYTES:
            row["status"] = "too_large"
            continue
        try:
            encoded = body.get("data")
            if not encoded and attachment_id:
                encoded = fetch_attachment(message_id, attachment_id).get("data")
            if not encoded:
                raise ValueError("missing attachment data")
            content = _decode_base64url(str(encoded))
            if len(content) > MAX_ATTACHMENT_BYTES:
                row["status"] = "too_large"
                continue
            text = _extract_text(filename, content)[:MAX_TEXT_CHARS]
            row["text"] = text
            row["status"] = "ok" if text else "empty"
        except Exception as exc:
            row["status"] = "error"
            row["error"] = type(exc).__name__
    return result


def _iter_parts(part: Mapping[str, Any]):
    yield part
    for child in part.get("parts") or []:
        yield from _iter_parts(child)


def _decode_base64url(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def _extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _pdf_text(content)
    if suffix == ".xlsx":
        _check_zip_size(content)
        return _xlsx_text(content)
    if suffix == ".xls":
        return _xls_text(content)
    if suffix == ".docx":
        _check_zip_size(content)
        return _docx_text(content)
    if suffix == ".doc":
        if content.startswith(b"PK\x03\x04"):
            _check_zip_size(content)
            return _docx_text(content)
        return _legacy_doc_text(content)
    if suffix in {".txt", ".csv", ".xml", ".html", ".htm"}:
        decoded = _decode_text(content)
        if suffix in {".html", ".htm"}:
            parser = _PlainTextHTMLParser()
            parser.feed(decoded)
            return parser.text()
        return decoded
    return ""


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content), strict=False)
    chunks: list[str] = []
    length = 0
    for page in reader.pages:
        value = page.extract_text() or ""
        if value:
            chunks.append(value)
            length += len(value)
        if length >= MAX_TEXT_CHARS:
            break
    return "\n".join(chunks)


def _xlsx_text(content: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        chunks: list[str] = []
        length = 0
        for sheet in workbook.worksheets:
            chunks.append(f"[{sheet.title}]")
            for cells in sheet.iter_rows(values_only=True):
                line = "\t".join(str(value) for value in cells if value is not None)
                if line:
                    chunks.append(line)
                    length += len(line)
                if length >= MAX_TEXT_CHARS:
                    return "\n".join(chunks)
        return "\n".join(chunks)
    finally:
        workbook.close()


def _xls_text(content: bytes) -> str:
    import xlrd

    workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    try:
        chunks: list[str] = []
        length = 0
        for sheet in workbook.sheets():
            chunks.append(f"[{sheet.name}]")
            for row_index in range(sheet.nrows):
                line = "\t".join(
                    str(value) for value in sheet.row_values(row_index) if value != ""
                )
                if line:
                    chunks.append(line)
                    length += len(line)
                if length >= MAX_TEXT_CHARS:
                    return "\n".join(chunks)
        return "\n".join(chunks)
    finally:
        workbook.release_resources()


def _docx_text(content: bytes) -> str:
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    chunks: list[str] = []
    length = 0
    with ZipFile(BytesIO(content)) as archive:
        names = [
            name for name in archive.namelist()
            if re.fullmatch(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", name)
        ]
        for name in names:
            root = ElementTree.fromstring(archive.read(name))
            for paragraph in root.iter(namespace + "p"):
                line = "".join(
                    (node.text or "") if node.tag == namespace + "t"
                    else "\t" if node.tag == namespace + "tab"
                    else "\n" if node.tag == namespace + "br"
                    else ""
                    for node in paragraph.iter()
                ).strip()
                if line:
                    chunks.append(line)
                    length += len(line)
                if length >= MAX_TEXT_CHARS:
                    return "\n".join(chunks)
    return "\n".join(chunks)


def _legacy_doc_text(content: bytes) -> str:
    # Old OLE .doc text can be compressed or UTF-16LE. This fallback extracts
    # readable runs without invoking Microsoft Word or executing macros.
    candidates = []
    for decoded in (
        content.decode("utf-16le", errors="ignore"),
        content.decode("cp1251", errors="ignore"),
    ):
        runs = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9 .,;:()/%+\-]{5,}", decoded)
        candidates.append("\n".join(run.strip() for run in runs if run.strip()))
    return max(candidates, key=len, default="")


def _check_zip_size(content: bytes) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("archive expansion limit exceeded")
    except BadZipFile:
        raise ValueError("invalid Office archive") from None


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored += 1
        elif tag in {"p", "br", "div", "tr", "li"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n\s*\n+", "\n", "".join(self._parts)).strip()
