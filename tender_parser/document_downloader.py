from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.eis import EIS_SOURCE_NAME, USER_AGENT


@dataclass(frozen=True)
class DocumentDownloadConfig:
    enabled: bool = False
    max_tenders: int = 10
    max_documents_per_tender: int = 10
    max_file_bytes: int = 25 * 1024 * 1024
    timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "DocumentDownloadConfig":
        return cls(
            enabled=_is_true(os.getenv("DOWNLOAD_TENDER_DOCUMENTS", "")),
            max_tenders=_positive_int(os.getenv("DOWNLOAD_MAX_TENDERS", ""), 10),
            max_documents_per_tender=_positive_int(
                os.getenv("DOWNLOAD_MAX_DOCUMENTS_PER_TENDER", ""), 10
            ),
            max_file_bytes=_positive_int(os.getenv("DOWNLOAD_MAX_FILE_MB", ""), 25)
            * 1024
            * 1024,
            timeout_seconds=_positive_int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", ""), 20),
        )


@dataclass(frozen=True)
class DocumentLink:
    url: str
    filename: str


@dataclass
class DocumentDownloadReport:
    status: str = "ok"
    attempted_tenders: int = 0
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def downloaded_count(self) -> int:
        return len(self.downloaded)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


class EisDocumentDownloader:
    def __init__(
        self,
        config: DocumentDownloadConfig,
        session: requests.Session | object | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False  # type: ignore[attr-defined]
        self.session.headers.update({"User-Agent": USER_AGENT})  # type: ignore[attr-defined]

    def download(
        self,
        tenders: list[TenderRecord],
        output_dir: Path,
    ) -> DocumentDownloadReport:
        if not self.config.enabled:
            return DocumentDownloadReport(status="disabled")

        report = DocumentDownloadReport()
        eligible = [
            tender
            for tender in tenders
            if tender.source == EIS_SOURCE_NAME and build_eis_documents_url(tender.url)
        ][: self.config.max_tenders]
        if not eligible:
            report.status = "no_eis_tenders"
            self._write_report(output_dir, report)
            return report

        for tender in eligible:
            report.attempted_tenders += 1
            self._download_tender(tender, output_dir, report)
        if report.errors and not report.downloaded:
            report.status = "error"
        elif report.errors:
            report.status = "partial"
        self._write_report(output_dir, report)
        return report

    def _download_tender(
        self,
        tender: TenderRecord,
        output_dir: Path,
        report: DocumentDownloadReport,
    ) -> None:
        documents_url = build_eis_documents_url(tender.url)
        if not documents_url:
            return
        try:
            response = get_with_retry(
                self.session,
                documents_url,
                timeout=self.config.timeout_seconds,
            )
            links = parse_eis_document_links(response.text, documents_url)[
                : self.config.max_documents_per_tender
            ]
        except requests.RequestException as exc:
            report.errors.append(f"{tender.tender_number or tender.url}: {exc.__class__.__name__}")
            return

        tender_id = _safe_component(tender.tender_number or tender.unique_key, limit=80)
        tender_dir = output_dir / EIS_SOURCE_NAME / tender_id
        for index, link in enumerate(links, start=1):
            filename = f"{index:03d}_{_safe_filename(link.filename)}"
            destination = tender_dir / filename
            relative = str(destination.relative_to(output_dir))
            if destination.exists() and destination.stat().st_size > 0:
                report.skipped.append(relative)
                continue
            try:
                self._download_file(link.url, destination)
                report.downloaded.append(relative)
            except (requests.RequestException, OSError, ValueError) as exc:
                report.errors.append(f"{relative}: {exc.__class__.__name__}")

    def _download_file(self, url: str, destination: Path) -> None:
        if not _is_public_eis_file_url(url):
            raise ValueError("unsupported document URL")
        response = self.session.get(  # type: ignore[attr-defined]
            url,
            timeout=self.config.timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        written = 0
        try:
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.config.max_file_bytes:
                        raise ValueError("document exceeds configured size limit")
                    handle.write(chunk)
            partial.replace(destination)
        finally:
            if partial.exists():
                partial.unlink()

    @staticmethod
    def _write_report(output_dir: Path, report: DocumentDownloadReport) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "download_report.json").write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def build_eis_documents_url(tender_url: str) -> str | None:
    parsed = urlparse(tender_url)
    if parsed.scheme != "https" or parsed.hostname != "zakupki.gov.ru":
        return None
    replaced = re.sub(r"/view/[^/?]+\.html$", "/view/documents.html", parsed.path)
    if replaced == parsed.path:
        return None
    return parsed._replace(path=replaced, fragment="").geturl()


def parse_eis_document_links(html: str, source_url: str) -> list[DocumentLink]:
    soup = BeautifulSoup(html, "html.parser")
    result: list[DocumentLink] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(source_url, str(anchor.get("href", "")))
        if not _is_public_eis_file_url(url) or url in seen:
            continue
        seen.add(url)
        label = str(anchor.get("title") or anchor.get_text(" ", strip=True) or "document")
        result.append(DocumentLink(url=url, filename=unquote(label)))
    return result


def _is_public_eis_file_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "zakupki.gov.ru"
        and parsed.path.startswith("/44fz/filestore/public/")
        and "/download/" in parsed.path
    )


def _safe_filename(value: str) -> str:
    cleaned = _safe_component(value, limit=150).rstrip(". ")
    return cleaned or "document.bin"


def _safe_component(value: str, *, limit: int) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", " ".join(value.split()))
    cleaned = cleaned.strip(". ")
    if cleaned.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        cleaned = f"_{cleaned}"
    return cleaned[:limit].rstrip(". ") or "unknown"


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
