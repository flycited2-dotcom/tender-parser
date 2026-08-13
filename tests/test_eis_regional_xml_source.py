from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from tender_parser.sources.eis_regional_xml import (
    EIS_REGIONAL_XML_SOURCE,
    EisRegionalXmlError,
    EisRegionalXmlSource,
    RemoteFile,
    parse_eis_archive,
    parse_eis_xml_document,
)
from tender_parser.sources.rts import SourceFetchError


def _notice_xml(
    *,
    number: str = "0175100000126000123",
    title: str = "Поставка серверного оборудования",
    published: str = "2026-08-10T09:30:00",
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ns2:export xmlns:ns2="http://zakupki.gov.ru/oos/types/1">
  <ns2:epNotificationEF2020>
    <ns2:commonInfo>
      <ns2:purchaseNumber>{number}</ns2:purchaseNumber>
      <ns2:purchaseObjectInfo>{title}</ns2:purchaseObjectInfo>
      <ns2:docPublishDate>{published}</ns2:docPublishDate>
      <ns2:href>https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={number}</ns2:href>
    </ns2:commonInfo>
    <ns2:customer><ns2:fullName>ГБУ ГОРОДА СЕВАСТОПОЛЯ</ns2:fullName></ns2:customer>
    <ns2:lot><ns2:maxPrice>1250000.50</ns2:maxPrice></ns2:lot>
    <ns2:procedureInfo>
      <ns2:submissionCloseDateTime>2026-08-20T12:00:00</ns2:submissionCloseDateTime>
    </ns2:procedureInfo>
    <ns2:deliveryPlace>
      <ns2:address>Адрес места поставки: г. Севастополь, ул. Ленина, 1</ns2:address>
    </ns2:deliveryPlace>
  </ns2:epNotificationEF2020>
</ns2:export>""".encode("utf-8")


def _zip_payload(*documents: tuple[str, bytes]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, document in documents:
            archive.writestr(name, document)
    return payload.getvalue()


def test_parse_namespaced_eis_notice_extracts_structured_fields() -> None:
    records = parse_eis_xml_document(
        _notice_xml(),
        region="Республика Крым",
        discovered_at=datetime(2026, 8, 13, 8, 0),
    )

    assert len(records) == 1
    tender = records[0]
    assert tender.source == EIS_REGIONAL_XML_SOURCE
    assert tender.tender_number == "0175100000126000123"
    assert tender.title == "Поставка серверного оборудования"
    assert tender.customer == "ГБУ ГОРОДА СЕВАСТОПОЛЯ"
    # Структурированное место поставки важнее каталога выгрузки.
    assert tender.region == "Севастополь"
    assert tender.price == 1_250_000.50
    assert tender.deadline == datetime(2026, 8, 20, 12, 0)
    assert tender.published_at == datetime(2026, 8, 10, 9, 30)
    assert tender.url.startswith("https://zakupki.gov.ru/")
    assert "структурированное место поставки" in tender.delivery_region_evidence
    assert tender.source_confidence == 0.98


def test_archive_keeps_latest_modification_and_deduplicates_by_number() -> None:
    payload = _zip_payload(
        ("first.xml", _notice_xml(title="Сервер", published="2026-08-09T10:00:00")),
        ("second.xml", _notice_xml(title="Сервер (изменение)", published="2026-08-11T10:00:00")),
    )

    records = parse_eis_archive(
        payload,
        region="Севастополь",
        source_path="/fcs_regions/Sevastopol/notifications/currMonth/data.zip",
    )

    assert len(records) == 1
    assert records[0].title == "Сервер (изменение)"
    assert records[0].published_at == datetime(2026, 8, 11, 10, 0)


def test_parse_actual_tff_collecting_end_dt_and_publish_dt_names() -> None:
    payload = _notice_xml().replace(
        b"<ns2:docPublishDate>2026-08-10T09:30:00</ns2:docPublishDate>",
        b"<ns2:docPublishDTInEIS>2026-08-10T09:30:00</ns2:docPublishDTInEIS>",
    ).replace(
        b"<ns2:submissionCloseDateTime>2026-08-20T12:00:00</ns2:submissionCloseDateTime>",
        b"<ns2:collectingEndDT>2026-08-20T12:00:00</ns2:collectingEndDT>",
    )

    record = parse_eis_xml_document(payload, region="Севастополь")[0]

    assert record.deadline == datetime(2026, 8, 20, 12, 0)
    assert record.published_at == datetime(2026, 8, 10, 9, 30)


def test_parser_rejects_dtd_and_entity_expansion() -> None:
    payload = b"""<?xml version='1.0'?>
<!DOCTYPE x [<!ENTITY bomb 'boom'>]>
<epNotification><purchaseNumber>0175100000126000123</purchaseNumber>
<purchaseObjectInfo>&bomb;</purchaseObjectInfo></epNotification>"""

    with pytest.raises(EisRegionalXmlError, match="DTD/ENTITY"):
        parse_eis_xml_document(payload, region="Республика Крым")


def test_archive_rejects_oversized_xml_before_parsing() -> None:
    payload = _zip_payload(("huge.xml", b"<root>" + b"x" * 2_000 + b"</root>"))

    with pytest.raises(EisRegionalXmlError, match="XML превышает лимит"):
        parse_eis_archive(
            payload,
            region="Республика Крым",
            source_path="notice.zip",
            max_xml_bytes=1_000,
            max_uncompressed_bytes=10_000,
        )


class FakeFtpClient:
    def __init__(
        self,
        *,
        files: list[RemoteFile] | None = None,
        payloads: dict[str, bytes] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.files = files or []
        self.payloads = payloads or {}
        self.list_error = list_error
        self.listed: list[str] = []
        self.downloaded: list[str] = []
        self.closed = False

    def list_files(self, directory: str) -> list[RemoteFile]:
        self.listed.append(directory)
        if self.list_error:
            raise self.list_error
        return self.files

    def download(self, remote: RemoteFile, max_bytes: int) -> bytes:
        self.downloaded.append(remote.path)
        payload = self.payloads[remote.path]
        if len(payload) > max_bytes:
            raise EisRegionalXmlError("too large")
        return payload

    def close(self) -> None:
        self.closed = True


def _source(
    client: FakeFtpClient,
    state_path: Path,
) -> EisRegionalXmlSource:
    return EisRegionalXmlSource(
        client=client,
        state_path=state_path,
        region_directories={"Севастополь": "Sevastopol"},
        now_factory=lambda: datetime(2026, 8, 13, 8, 0),
    )


def test_source_checkpoints_archive_and_reuses_last_good_snapshot(tmp_path: Path) -> None:
    remote = RemoteFile(
        "/fcs_regions/Sevastopol/notifications/currMonth/notification_1.xml.zip",
        size=1_000,
        modified_at=datetime(2026, 8, 13, 7, 0),
    )
    archive = _zip_payload(("notice.xml", _notice_xml()))
    state_path = tmp_path / "state.json"
    first_client = FakeFtpClient(files=[remote], payloads={remote.path: archive})

    first = _source(first_client, state_path).fetch_with_report([])

    assert len(first.tenders) == 1
    assert first.health[0].status == "ok"
    assert first_client.downloaded == [remote.path]
    assert first_client.closed is True
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert remote.signature in state["processed"]

    second_client = FakeFtpClient(files=[remote], payloads={remote.path: archive})
    second = _source(second_client, state_path).fetch_with_report([])

    assert len(second.tenders) == 1
    assert second.health[0].status == "ok"
    assert second_client.downloaded == []
    assert "новых архивов 0/0" in second.health[0].detail


def test_source_returns_cached_snapshot_as_partial_when_ftp_is_down(tmp_path: Path) -> None:
    remote = RemoteFile(
        "/fcs_regions/Sevastopol/notifications/currMonth/notice.xml.zip",
        size=1_000,
    )
    archive = _zip_payload(("notice.xml", _notice_xml()))
    state_path = tmp_path / "state.json"
    _source(
        FakeFtpClient(files=[remote], payloads={remote.path: archive}), state_path
    ).fetch_with_report([])

    unavailable = FakeFtpClient(list_error=OSError("DNS unavailable"))
    result = _source(unavailable, state_path).fetch_with_report([])

    assert len(result.tenders) == 1
    assert result.health[0].status == "partial"
    assert result.errors
    assert "каталог недоступен" in result.errors[0]


def test_source_imports_manually_downloaded_archive_from_region_folder(tmp_path: Path) -> None:
    import_dir = tmp_path / "imports" / "eis_xml"
    region_dir = import_dir / "Sevastopol"
    region_dir.mkdir(parents=True)
    archive_path = region_dir / "manual.xml.zip"
    archive_path.write_bytes(_zip_payload(("notice.xml", _notice_xml())))
    client = FakeFtpClient()
    source = EisRegionalXmlSource(
        client=client,
        state_path=tmp_path / "state.json",
        import_dir=import_dir,
        region_directories={"Севастополь": "Sevastopol"},
        now_factory=lambda: datetime(2026, 8, 13, 8, 0),
    )

    result = source.fetch_with_report([])

    assert len(result.tenders) == 1
    assert result.health[0].status == "ok"
    assert "локальный импорт 1" in result.health[0].detail
    # Локальный архив читается без передачи его пути FTP-клиенту.
    assert client.downloaded == []


def test_source_feature_flag_can_skip_without_network(tmp_path: Path) -> None:
    client = FakeFtpClient(list_error=AssertionError("network must not be called"))
    source = EisRegionalXmlSource(
        client=client,
        state_path=tmp_path / "state.json",
        enabled=False,
    )

    result = source.fetch_with_report([])

    assert result.tenders == []
    assert result.errors == []
    assert result.health[0].status == "skipped"
    assert client.listed == []
    assert client.closed is True


def test_failed_archive_is_not_checkpointed_and_will_be_retried(tmp_path: Path) -> None:
    remote = RemoteFile(
        "/fcs_regions/Sevastopol/notifications/currMonth/broken.zip",
        size=10,
    )
    client = FakeFtpClient(files=[remote], payloads={remote.path: b"not xml"})
    state_path = tmp_path / "state.json"

    result = _source(client, state_path).fetch_with_report([])

    assert result.health[0].status == "partial"
    assert result.errors
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert remote.signature not in state["processed"]


def test_fetch_keywords_raises_only_when_no_cache_and_ftp_is_unavailable(tmp_path: Path) -> None:
    source = _source(
        FakeFtpClient(list_error=OSError("DNS unavailable")),
        tmp_path / "missing.json",
    )

    with pytest.raises(SourceFetchError, match="каталог недоступен"):
        source.fetch_keywords(["сервер"])
