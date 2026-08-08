from pathlib import Path

from tender_parser.document_downloader import (
    DocumentDownloadConfig,
    EisDocumentDownloader,
    build_eis_documents_url,
    parse_eis_document_links,
)
from tender_parser.models import TenderRecord


DOCUMENTS_HTML = """
<html><body>
  <a href="https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=ABC"
     title="Описание объекта закупки.docx">Описание объекта закупки</a>
  <a href="/44fz/filestore/public/1.0/download/priz/file.html?uid=DEF"
     title="Расчет НМЦК.xlsx">Расчет НМЦК</a>
  <a href="https://evil.test/download/file.exe" title="Не скачивать.exe">Чужой файл</a>
  <a href="/epz/main/public/document/view.html?sectionId=1">Материалы ЕИС</a>
</body></html>
"""


def make_tender() -> TenderRecord:
    return TenderRecord(
        title="Поставка МФУ",
        url=(
            "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
            "?regNumber=0372100013226000021"
        ),
        source="eis-zakupki",
        tender_number="0372100013226000021",
        review_priority="hot",
    )


def test_build_eis_documents_url_replaces_current_tab() -> None:
    assert build_eis_documents_url(make_tender().url) == (
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html"
        "?regNumber=0372100013226000021"
    )


def test_parse_eis_document_links_keeps_only_public_eis_filestore() -> None:
    links = parse_eis_document_links(
        DOCUMENTS_HTML,
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html?regNumber=1",
    )

    assert [link.filename for link in links] == [
        "Описание объекта закупки.docx",
        "Расчет НМЦК.xlsx",
    ]
    assert all("zakupki.gov.ru/44fz/filestore/" in link.url for link in links)


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield self.content


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.trust_env = True
        self.requested: list[str] = []

    def get(self, url: str, *, timeout: int, stream: bool = False) -> FakeResponse:
        self.requested.append(url)
        if "documents.html" in url:
            return FakeResponse(text=DOCUMENTS_HTML)
        if "uid=ABC" in url:
            return FakeResponse(content=b"docx-content")
        return FakeResponse(content=b"xlsx-content")


def test_downloader_saves_documents_in_tender_folder_and_skips_existing(tmp_path: Path) -> None:
    session = FakeSession()
    config = DocumentDownloadConfig(enabled=True, max_tenders=5, max_documents_per_tender=5)
    downloader = EisDocumentDownloader(config, session=session)

    first = downloader.download([make_tender()], tmp_path / "downloads")
    second = downloader.download([make_tender()], tmp_path / "downloads")

    tender_dir = tmp_path / "downloads" / "eis-zakupki" / "0372100013226000021"
    assert first.downloaded_count == 2
    assert second.downloaded_count == 0
    assert second.skipped_count == 2
    assert (tender_dir / "001_Описание объекта закупки.docx").read_bytes() == b"docx-content"
    assert (tender_dir / "002_Расчет НМЦК.xlsx").read_bytes() == b"xlsx-content"
    assert (tmp_path / "downloads" / "download_report.json").exists()


def test_downloader_does_nothing_when_disabled(tmp_path: Path) -> None:
    result = EisDocumentDownloader(DocumentDownloadConfig(enabled=False)).download(
        [make_tender()], tmp_path / "downloads"
    )

    assert result.status == "disabled"
    assert not (tmp_path / "downloads").exists()
