from __future__ import annotations

import ftplib
import io
import json
import os
import posixpath
import re
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol
from urllib.parse import urlencode, urljoin, urlparse
from xml.etree import ElementTree

from tender_parser.models import TenderRecord
from tender_parser.regions import detect_delivery_region
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import normalize_text, parse_price_rub


# Массовая выгрузка открытых данных ЕИС. Доступ к ней и назначение ЕИС
# документированы Федеральным казначейством:
# https://roskazna.gov.ru/gis/eis-zakupki-gov-ru
# Описание актуальных форматов 44-ФЗ публикуется самой ЕИС:
# https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=6&pageNo=1&categories=FZ44&_categories=on
OFFICIAL_FTP_HOST = "ftp.zakupki.gov.ru"
OFFICIAL_FTP_USER = "free"
OFFICIAL_FTP_PASSWORD = "free"
EIS_REGIONAL_XML_SOURCE = "eis-regional-xml"
EIS_SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html"

# Имена субъектов в каталоге ЕИС исторические и не совпадают с отображаемыми
# названиями. Переопределение поддерживается конструктором без изменения кода.
DEFAULT_REGION_DIRECTORIES: dict[str, str] = {
    "Республика Крым": "Krim_Resp",
    "Севастополь": "Sevastopol",
    "Запорожская область": "Zaporozhskaja_obl",
    "Херсонская область": "Hersonskaja_obl",
}

_ALLOWED_REMOTE_SUFFIXES = (".xml", ".xml.zip", ".zip")
_NUMBER_TAGS = ("purchaseNumber", "notificationNumber", "registryNumber", "regNumber")
_TITLE_TAGS = ("purchaseObjectInfo", "purchaseName", "objectInfo", "subject", "name")
_CUSTOMER_TAGS = (
    "customerFullName",
    "customerName",
    "organizationName",
    "fullName",
)
_PRICE_TAGS = ("maxPrice", "initialMaxPrice", "lotMaxPrice", "initialSum", "price")
_DEADLINE_TAGS = (
    "submissionCloseDateTime",
    "applicationEndDateTime",
    "collectingEndDateTime",
    "collectingEndDT",
    "newCollectingEndDT",
    "submissionCloseDate",
    "applicationEndDate",
    "collectingEndDate",
    "endDate",
)
_PUBLISHED_TAGS = (
    "docPublishDate",
    "docPublishDTInEIS",
    "notificationPublishDate",
    "purchasePublishDate",
    "publishDate",
    "publicationDate",
    "createDate",
)
_STATUS_TAGS = ("status", "state", "modificationType")
_URL_TAGS = ("href", "noticeUrl", "url")


class EisRegionalXmlError(RuntimeError):
    """Ошибка безопасного чтения официальной региональной выгрузки."""


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int | None = None
    modified_at: datetime | None = None

    @property
    def signature(self) -> str:
        modified = self.modified_at.isoformat() if self.modified_at else ""
        return f"{self.path}|{self.size if self.size is not None else ''}|{modified}"


@dataclass(frozen=True)
class ArchiveCandidate:
    region: str
    remote: RemoteFile
    local_path: Path | None = None


class EisFtpClient(Protocol):
    def list_files(self, directory: str) -> list[RemoteFile]:
        ...

    def download(self, remote: RemoteFile, max_bytes: int) -> bytes:
        ...

    def close(self) -> None:
        ...


class OfficialEisFtpClient:
    """Минимальный FTP-клиент только для публичного каталога fcs_regions."""

    def __init__(
        self,
        host: str = OFFICIAL_FTP_HOST,
        timeout_seconds: int = 20,
    ) -> None:
        self.host = host
        self.timeout_seconds = timeout_seconds
        self._ftp: ftplib.FTP | None = None

    def list_files(self, directory: str) -> list[RemoteFile]:
        safe_directory = _safe_remote_directory(directory)
        ftp = self._connection()
        try:
            entries = list(ftp.mlsd(safe_directory, facts=["type", "size", "modify"]))
        except (ftplib.error_perm, ftplib.error_temp, AttributeError, TypeError):
            # Старые FTP-серверы могут не поддерживать MLSD. NLST остаётся
            # официальным read-only fallback; размер читаем отдельно.
            names = ftp.nlst(safe_directory)
            result: list[RemoteFile] = []
            for value in names:
                path = (
                    value
                    if value.startswith("/")
                    else posixpath.join(safe_directory, posixpath.basename(value))
                )
                if not _is_supported_remote_file(path):
                    continue
                try:
                    size = ftp.size(path)
                except ftplib.all_errors:
                    size = None
                result.append(RemoteFile(path=path, size=size))
            return result

        result = []
        for name, facts in entries:
            if facts.get("type") != "file":
                continue
            path = posixpath.join(safe_directory, name)
            if not _is_supported_remote_file(path):
                continue
            size = _optional_int(facts.get("size"))
            modified = _parse_ftp_datetime(facts.get("modify", ""))
            result.append(RemoteFile(path=path, size=size, modified_at=modified))
        return result

    def download(self, remote: RemoteFile, max_bytes: int) -> bytes:
        path = _safe_remote_file(remote.path)
        if remote.size is not None and remote.size > max_bytes:
            raise EisRegionalXmlError(
                f"архив превышает лимит {max_bytes} байт: {posixpath.basename(path)}"
            )

        payload = bytearray()

        def receive(chunk: bytes) -> None:
            if len(payload) + len(chunk) > max_bytes:
                # Соединение после прерванного RETR может быть рассинхронизировано.
                self.close()
                raise EisRegionalXmlError(
                    f"архив превысил лимит {max_bytes} байт при загрузке"
                )
            payload.extend(chunk)

        self._connection().retrbinary(f"RETR {path}", receive, blocksize=64 * 1024)
        return bytes(payload)

    def close(self) -> None:
        ftp, self._ftp = self._ftp, None
        if ftp is None:
            return
        try:
            ftp.quit()
        except ftplib.all_errors:
            try:
                ftp.close()
            except OSError:
                pass

    def _connection(self) -> ftplib.FTP:
        if self._ftp is not None:
            return self._ftp
        ftp = ftplib.FTP()
        ftp.encoding = "utf-8"
        ftp.connect(self.host, 21, timeout=self.timeout_seconds)
        ftp.login(OFFICIAL_FTP_USER, OFFICIAL_FTP_PASSWORD)
        ftp.set_pasv(True)
        self._ftp = ftp
        return ftp


class EisRegionalXmlSource:
    source_name = EIS_REGIONAL_XML_SOURCE

    def __init__(
        self,
        *,
        client: EisFtpClient | None = None,
        state_path: Path | str | None = None,
        import_dir: Path | str | None = None,
        region_directories: dict[str, str] | None = None,
        enabled: bool = True,
        max_files_per_region: int = 8,
        max_files_per_run: int = 24,
        max_archive_bytes: int = 25 * 1024 * 1024,
        max_total_download_bytes: int = 100 * 1024 * 1024,
        max_xml_bytes: int = 12 * 1024 * 1024,
        max_uncompressed_bytes: int = 80 * 1024 * 1024,
        max_zip_members: int = 2_000,
        max_zip_ratio: int = 200,
        max_cached_records: int = 5_000,
        cache_retention_days: int = 90,
        previous_month_days: int = 7,
        now_factory: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.client = client or OfficialEisFtpClient()
        configured_state = os.getenv("EIS_XML_STATE_PATH", "").strip()
        self.state_path = Path(state_path or configured_state or "data/eis_regional_xml_state.json")
        configured_import = os.getenv("EIS_XML_IMPORT_DIR", "").strip()
        self.import_dir = Path(import_dir or configured_import or "imports/eis_xml")
        self.region_directories = dict(region_directories or DEFAULT_REGION_DIRECTORIES)
        self.enabled = enabled
        self.max_files_per_region = max(1, min(max_files_per_region, 100))
        self.max_files_per_run = max(1, min(max_files_per_run, 200))
        self.max_archive_bytes = max(64 * 1024, max_archive_bytes)
        self.max_total_download_bytes = max(self.max_archive_bytes, max_total_download_bytes)
        self.max_xml_bytes = max(64 * 1024, max_xml_bytes)
        self.max_uncompressed_bytes = max(self.max_xml_bytes, max_uncompressed_bytes)
        self.max_zip_members = max(1, min(max_zip_members, 20_000))
        self.max_zip_ratio = max(2, min(max_zip_ratio, 1_000))
        self.max_cached_records = max(1, min(max_cached_records, 50_000))
        self.cache_retention_days = max(1, min(cache_retention_days, 366))
        self.previous_month_days = max(0, min(previous_month_days, 15))
        self.now_factory = now_factory

    @classmethod
    def from_env(cls, **kwargs: object) -> "EisRegionalXmlSource":
        """Создаёт источник с feature flag ``EIS_XML_ENABLED`` (по умолчанию off)."""
        kwargs.setdefault("enabled", eis_regional_xml_enabled())
        return cls(**kwargs)

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        result = self.fetch_with_report(keywords)
        if not result.tenders and result.errors:
            raise SourceFetchError(result.errors[0])
        return result.tenders

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        # Категории применяются общим downstream-фильтром. Здесь читается вся
        # официальная региональная лента, чтобы не терять морфологические формы.
        del keywords
        started = monotonic()
        if not self.enabled:
            self.client.close()
            return SourceFetchResult(
                health=[
                    SourceHealth(
                        source=self.source_name,
                        status="skipped",
                        found=0,
                        elapsed_seconds=round(monotonic() - started, 3),
                        detail="отключён feature flag EIS_XML_ENABLED",
                    )
                ]
            )
        now = self.now_factory()
        state, state_warnings = _load_state(self.state_path)
        processed = _processed_from_state(state.get("processed"))
        cached = _records_from_state(state.get("records", []))
        errors: list[str] = []
        warnings = list(state_warnings)
        listed_region_count = 0
        candidates: list[ArchiveCandidate] = []
        local_candidate_count = 0

        months = ["currMonth"]
        if now.day <= self.previous_month_days:
            months.append("prevMonth")

        try:
            for region, region_directory in self.region_directories.items():
                for remote, local_path in _list_local_archives(
                    self.import_dir, region=region, region_directory=region_directory
                ):
                    if remote.signature not in processed:
                        candidates.append(
                            ArchiveCandidate(
                                region=region,
                                remote=remote,
                                local_path=local_path,
                            )
                        )
                        local_candidate_count += 1

                region_files: list[RemoteFile] = []
                current_month_listed = False
                for month in months:
                    directory = (
                        f"/fcs_regions/{region_directory}/notifications/{month}"
                    )
                    try:
                        listed = self.client.list_files(directory)
                    except Exception as exc:  # FTP implementations expose several exception types.
                        message = _safe_error(exc)
                        if month == "currMonth":
                            errors.append(f"ЕИС XML, {region}: каталог недоступен ({message})")
                        else:
                            warnings.append(f"{region}: prevMonth недоступен ({message})")
                        continue
                    if month == "currMonth":
                        current_month_listed = True
                    region_files.extend(listed)
                if current_month_listed:
                    listed_region_count += 1

                unseen = [item for item in region_files if item.signature not in processed]
                unseen.sort(key=_remote_file_sort_key, reverse=True)
                candidates.extend(
                    ArchiveCandidate(region=region, remote=item)
                    for item in unseen[: self.max_files_per_region]
                )

            candidates.sort(
                key=lambda item: _remote_file_sort_key(item.remote), reverse=True
            )
            candidates = candidates[: self.max_files_per_run]

            downloaded_bytes = 0
            parsed_files = 0
            new_records: list[TenderRecord] = []
            for candidate in candidates:
                region, remote = candidate.region, candidate.remote
                if remote.size is not None and downloaded_bytes + remote.size > self.max_total_download_bytes:
                    warnings.append("достигнут суммарный лимит загрузки; остаток перенесён на следующий запуск")
                    break
                try:
                    payload = (
                        _read_local_archive(candidate.local_path, self.max_archive_bytes)
                        if candidate.local_path is not None
                        else self.client.download(remote, self.max_archive_bytes)
                    )
                    downloaded_bytes += len(payload)
                    archive_records = parse_eis_archive(
                        payload,
                        region=region,
                        source_path=remote.path,
                        max_xml_bytes=self.max_xml_bytes,
                        max_uncompressed_bytes=self.max_uncompressed_bytes,
                        max_zip_members=self.max_zip_members,
                        max_zip_ratio=self.max_zip_ratio,
                        discovered_at=now,
                    )
                except Exception as exc:
                    errors.append(
                        f"ЕИС XML, {region}, {posixpath.basename(remote.path)}: {_safe_error(exc)}"
                    )
                    # Не ставим checkpoint: повреждённый/оборванный архив можно
                    # безопасно повторить на следующем запуске.
                    continue

                new_records.extend(archive_records)
                processed[remote.signature] = now.isoformat(timespec="seconds")
                parsed_files += 1

            merged = _merge_records([*cached, *new_records])
            merged = _prune_records(
                merged,
                now=now,
                retention_days=self.cache_retention_days,
                limit=self.max_cached_records,
            )

            state_saved = True
            if parsed_files or not self.state_path.exists():
                processed = _trim_processed(processed, limit=10_000)
                try:
                    _save_state(
                        self.state_path,
                        processed=processed,
                        records=merged,
                        updated_at=now,
                    )
                except OSError as exc:
                    state_saved = False
                    errors.append(f"ЕИС XML: checkpoint не сохранён ({_safe_error(exc)})")

            status = _health_status(
                records=merged,
                errors=errors,
                listed_region_count=listed_region_count,
                expected_region_count=len(self.region_directories),
            )
            detail_parts = [
                f"регионов доступно {listed_region_count}/{len(self.region_directories)}",
                f"новых архивов {parsed_files}/{len(candidates)}",
                f"локальный импорт {local_candidate_count}",
                f"снимок {len(merged)}",
                f"загружено {downloaded_bytes} байт",
            ]
            if warnings:
                detail_parts.append("предупреждения: " + "; ".join(warnings[:3]))
            if not state_saved:
                detail_parts.append("повтор архивов возможен: checkpoint не записан")
            if errors:
                detail_parts.append("ошибок: " + str(len(errors)))
            return SourceFetchResult(
                tenders=merged,
                health=[
                    SourceHealth(
                        source=self.source_name,
                        status=status,
                        found=len(merged),
                        elapsed_seconds=round(monotonic() - started, 3),
                        detail="; ".join(detail_parts),
                    )
                ],
                errors=errors,
            )
        finally:
            self.client.close()


def parse_eis_archive(
    payload: bytes,
    *,
    region: str,
    source_path: str,
    max_xml_bytes: int = 12 * 1024 * 1024,
    max_uncompressed_bytes: int = 80 * 1024 * 1024,
    max_zip_members: int = 2_000,
    max_zip_ratio: int = 200,
    discovered_at: datetime | None = None,
) -> list[TenderRecord]:
    """Читает XML или ZIP полностью в памяти, не извлекая пути из архива."""
    discovered = discovered_at or datetime.now()
    if zipfile.is_zipfile(io.BytesIO(payload)):
        documents: list[tuple[str, bytes]] = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            xml_members = [item for item in members if item.filename.lower().endswith(".xml")]
            if len(members) > max_zip_members:
                raise EisRegionalXmlError(f"в ZIP слишком много файлов: {len(members)}")
            if not xml_members:
                raise EisRegionalXmlError("в ZIP нет XML-файлов")
            total_size = sum(item.file_size for item in xml_members)
            if total_size > max_uncompressed_bytes:
                raise EisRegionalXmlError(
                    f"распакованный XML превышает лимит {max_uncompressed_bytes} байт"
                )
            for item in xml_members:
                if item.file_size > max_xml_bytes:
                    raise EisRegionalXmlError(
                        f"XML превышает лимит {max_xml_bytes} байт: {posixpath.basename(item.filename)}"
                    )
                compressed = max(item.compress_size, 1)
                if item.file_size > 64 * 1024 and item.file_size / compressed > max_zip_ratio:
                    raise EisRegionalXmlError(
                        f"подозрительная степень сжатия ZIP: {posixpath.basename(item.filename)}"
                    )
                with archive.open(item, "r") as stream:
                    document = stream.read(max_xml_bytes + 1)
                if len(document) > max_xml_bytes:
                    raise EisRegionalXmlError("XML превысил лимит при распаковке")
                documents.append((item.filename, document))
    else:
        if len(payload) > max_xml_bytes:
            raise EisRegionalXmlError(f"XML превышает лимит {max_xml_bytes} байт")
        documents = [(source_path, payload)]

    records: list[TenderRecord] = []
    for document_name, document in documents:
        records.extend(
            parse_eis_xml_document(
                document,
                region=region,
                source_path=f"{source_path}#{document_name}",
                discovered_at=discovered,
            )
        )
    return _merge_records(records)


def parse_eis_xml_document(
    payload: bytes,
    *,
    region: str,
    source_path: str = "",
    discovered_at: datetime | None = None,
) -> list[TenderRecord]:
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise EisRegionalXmlError("DTD/ENTITY запрещены")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise EisRegionalXmlError(f"невалидный XML: {exc}") from exc

    candidates = [
        element
        for element in root.iter()
        if "notification" in _local_name(element.tag).lower()
        and _first_text(element, _NUMBER_TAGS)
    ]
    if not candidates and _first_text(root, _NUMBER_TAGS):
        candidates = [root]

    records: list[TenderRecord] = []
    seen: set[str] = set()
    for element in candidates:
        number = _clean_number(_first_text(element, _NUMBER_TAGS))
        title = _first_text(element, _TITLE_TAGS)
        if not number or number in seen:
            continue
        seen.add(number)
        title = title or f"Извещение ЕИС № {number}"
        raw_text = _element_text(element, max_chars=8_000)
        structured_delivery = _delivery_text(element)
        delivery_region = detect_delivery_region(structured_delivery) or detect_delivery_region(raw_text)
        canonical_region = delivery_region or region
        url = _safe_eis_url(_first_text(element, _URL_TAGS), tender_number=number)
        deadline_value = _first_text(element, _DEADLINE_TAGS)
        deadline_time = _first_text(
            element,
            (
                "submissionCloseTime",
                "applicationEndTime",
                "collectingEndTime",
                "endTime",
            ),
        )
        records.append(
            TenderRecord(
                title=title,
                url=url,
                source=EIS_REGIONAL_XML_SOURCE,
                tender_number=number,
                customer=_customer_text(element) or None,
                region=canonical_region,
                # В TFF сумма хранится числом без подписи валюты; набор 44-ФЗ
                # для этих полей номинирован в рублях.
                price=parse_price_rub(
                    _first_text(element, _PRICE_TAGS), require_currency=False
                ),
                deadline=_parse_datetime_parts(deadline_value, deadline_time),
                status=_notice_status(element),
                published_at=_parse_datetime(_first_text(element, _PUBLISHED_TAGS)),
                discovered_at=discovered_at or datetime.now(),
                raw_text=raw_text,
                delivery_region_evidence=(
                    f"ЕИС XML, структурированное место поставки: {structured_delivery[:500]}"
                    if delivery_region and structured_delivery
                    else ""
                ),
                source_confidence=0.98,
            )
        )
    return records


def _delivery_text(element: ElementTree.Element) -> str:
    values: list[str] = []
    for child in element.iter():
        name = _local_name(child.tag).lower()
        if any(marker in name for marker in ("deliveryplace", "deliveryaddress", "placeofdelivery")):
            text = _element_text(child, max_chars=1_500)
            if text:
                values.append(f"место поставки: {text}")
    return " ".join(values[:5])


def _customer_text(element: ElementTree.Element) -> str:
    # В TFF много полей fullName (оператор ЭТП, подписант, заказчик). Сначала
    # ограничиваем поиск customer-узлом, чтобы не приписать закупку оператору.
    for child in element.iter():
        local = _local_name(child.tag).lower()
        if local in {"customer", "customerinfo", "customerrequirement"}:
            value = _first_text(child, _CUSTOMER_TAGS)
            if value:
                return value
    return _first_text(element, _CUSTOMER_TAGS[:-1])


def _notice_status(element: ElementTree.Element) -> str:
    explicit = _first_text(element, _STATUS_TAGS)
    if explicit:
        return explicit
    root_name = _local_name(element.tag).lower()
    if "cancel" in root_name:
        return "Отменено"
    if "change" in root_name or "modif" in root_name:
        return "Изменено"
    return "Опубликовано в ЕИС"


def _first_text(element: ElementTree.Element, names: tuple[str, ...]) -> str:
    by_name: dict[str, list[str]] = {name.lower(): [] for name in names}
    for child in element.iter():
        local = _local_name(child.tag).lower()
        if local not in by_name:
            continue
        value = " ".join(part.strip() for part in child.itertext() if part.strip())
        if value:
            by_name[local].append(value)
    for name in names:
        values = by_name[name.lower()]
        if values:
            return values[0]
    return ""


def _element_text(element: ElementTree.Element, max_chars: int) -> str:
    result: list[str] = []
    length = 0
    for part in element.itertext():
        normalized = " ".join(part.split())
        if not normalized:
            continue
        remaining = max_chars - length
        if remaining <= 0:
            break
        value = normalized[:remaining]
        result.append(value)
        length += len(value) + 1
    return " ".join(result)


def _parse_datetime(value: str, end_of_day: bool = False) -> datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    iso_value = cleaned.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        if end_of_day and "T" not in cleaned and " " not in cleaned:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if end_of_day and fmt in {"%d.%m.%Y", "%Y-%m-%d"}:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except ValueError:
            continue
    return None


def _parse_datetime_parts(date_value: str, time_value: str) -> datetime | None:
    has_time = bool(re.search(r"[T ]\d{1,2}:\d{2}", date_value))
    parsed = _parse_datetime(date_value, end_of_day=not time_value and not has_time)
    if parsed is None or not time_value or has_time:
        return parsed
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", time_value)
    if not match:
        return parsed
    return parsed.replace(
        hour=int(match.group(1)),
        minute=int(match.group(2)),
        second=int(match.group(3) or 0),
    )


def _safe_eis_url(value: str, tender_number: str) -> str:
    if value:
        absolute = urljoin("https://zakupki.gov.ru/", value.strip())
        parsed = urlparse(absolute)
        if parsed.scheme == "https" and (
            parsed.hostname == "zakupki.gov.ru" or parsed.hostname and parsed.hostname.endswith(".zakupki.gov.ru")
        ):
            return absolute
    return f"{EIS_SEARCH_URL}?{urlencode({'searchString': tender_number})}"


def eis_regional_xml_enabled() -> bool:
    return os.getenv("EIS_XML_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _list_local_archives(
    root: Path, *, region: str, region_directory: str
) -> list[tuple[RemoteFile, Path]]:
    if not root.exists():
        return []
    root_resolved = root.resolve()
    results: list[tuple[RemoteFile, Path]] = []
    seen: set[Path] = set()
    for directory in (root / region_directory, root / region):
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            paths = list(directory.iterdir())
        except OSError:
            continue
        for path in paths:
            if path.is_symlink() or not path.is_file() or not _is_supported_remote_file(path.name):
                continue
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(root_resolved)
            except ValueError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                stat = resolved.stat()
            except OSError:
                continue
            remote = RemoteFile(
                path="/local_import/" + relative.as_posix(),
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
            )
            results.append((remote, resolved))
    results.sort(key=lambda item: _remote_file_sort_key(item[0]), reverse=True)
    return results


def _read_local_archive(path: Path | None, max_bytes: int) -> bytes:
    if path is None:
        raise EisRegionalXmlError("локальный путь архива не задан")
    with path.open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise EisRegionalXmlError(f"локальный архив превышает лимит {max_bytes} байт")
    return payload


def _merge_records(records: list[TenderRecord]) -> list[TenderRecord]:
    merged: dict[str, TenderRecord] = {}
    for record in records:
        key = record.tender_number or record.url
        current = merged.get(key)
        if current is None:
            merged[key] = record
            continue
        current_date = current.published_at or datetime.min
        record_date = record.published_at or datetime.min
        preferred, alternate = (record, current) if record_date >= current_date else (current, record)
        title = preferred.title
        if title.startswith("Извещение ЕИС №") and not alternate.title.startswith(
            "Извещение ЕИС №"
        ):
            title = alternate.title
        merged[key] = replace(
            preferred,
            title=title,
            customer=preferred.customer or alternate.customer,
            region=preferred.region or alternate.region,
            price=preferred.price if preferred.price is not None else alternate.price,
            deadline=preferred.deadline or alternate.deadline,
            status=preferred.status or alternate.status,
            raw_text=preferred.raw_text or alternate.raw_text,
            delivery_region_evidence=(
                preferred.delivery_region_evidence or alternate.delivery_region_evidence
            ),
            source_confidence=max(preferred.source_confidence, alternate.source_confidence),
        )
    return sorted(
        merged.values(),
        key=lambda item: (item.published_at or datetime.min, item.tender_number or ""),
        reverse=True,
    )


def _prune_records(
    records: list[TenderRecord], *, now: datetime, retention_days: int, limit: int
) -> list[TenderRecord]:
    cutoff = now - timedelta(days=retention_days)
    retained = [
        record
        for record in records
        if (record.deadline is not None and record.deadline >= now - timedelta(days=7))
        or (record.published_at is not None and record.published_at >= cutoff)
        or (record.deadline is None and record.published_at is None)
    ]
    return _merge_records(retained)[:limit]


def _record_to_state(record: TenderRecord) -> dict[str, object]:
    payload = asdict(record)
    for name in ("deadline", "published_at", "discovered_at"):
        value = payload.get(name)
        payload[name] = value.isoformat() if isinstance(value, datetime) else None
    # XML содержит много служебных узлов; для восстановления карточки хватает
    # ограниченного текста, а checkpoint остаётся разумного размера.
    payload["raw_text"] = str(payload.get("raw_text", ""))[:8_000]
    return payload


def _record_from_state(payload: object) -> TenderRecord | None:
    if not isinstance(payload, dict):
        return None
    try:
        values = dict(payload)
        for name in ("deadline", "published_at", "discovered_at"):
            value = values.get(name)
            values[name] = _parse_datetime(value) if isinstance(value, str) else None
        values["matched_terms"] = list(values.get("matched_terms") or [])
        values["document_matches"] = list(values.get("document_matches") or [])
        return TenderRecord(**values)
    except (TypeError, ValueError):
        return None


def _records_from_state(payload: object) -> list[TenderRecord]:
    if not isinstance(payload, list):
        return []
    return [record for item in payload if (record := _record_from_state(item)) is not None]


def _processed_from_state(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, (str, int, float))
    }


def _load_state(path: Path) -> tuple[dict[str, object], list[str]]:
    if not path.exists():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"checkpoint не прочитан ({_safe_error(exc)})"]
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {}, ["checkpoint имеет неизвестный формат"]
    return payload, []


def _save_state(
    path: Path,
    *,
    processed: dict[str, object],
    records: list[TenderRecord],
    updated_at: datetime,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": 1,
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "processed": processed,
        "records": [_record_to_state(record) for record in records],
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _trim_processed(processed: dict[str, object], limit: int) -> dict[str, object]:
    return dict(
        sorted(processed.items(), key=lambda pair: str(pair[1]), reverse=True)[:limit]
    )


def _health_status(
    *,
    records: list[TenderRecord],
    errors: list[str],
    listed_region_count: int,
    expected_region_count: int,
) -> str:
    if errors or listed_region_count < expected_region_count:
        return "partial" if records or listed_region_count else "error"
    return "ok" if records else "empty"


def _safe_remote_directory(value: str) -> str:
    normalized = posixpath.normpath("/" + value.lstrip("/"))
    if not normalized.startswith("/fcs_regions/") or ".." in value.split("/"):
        raise EisRegionalXmlError("запрещённый путь FTP")
    return normalized


def _safe_remote_file(value: str) -> str:
    path = _safe_remote_directory(value)
    if not _is_supported_remote_file(path):
        raise EisRegionalXmlError("неподдерживаемый тип файла FTP")
    return path


def _is_supported_remote_file(value: str) -> bool:
    return value.lower().endswith(_ALLOWED_REMOTE_SUFFIXES)


def _remote_file_sort_key(remote: RemoteFile) -> tuple[datetime, str]:
    return remote.modified_at or datetime.min, remote.path


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _parse_ftp_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _clean_number(value: str) -> str:
    match = re.search(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9/-]{5,}", value.strip())
    return match.group(0) if match else ""


def _safe_error(exc: object) -> str:
    value = normalize_text(str(exc)).replace(OFFICIAL_FTP_PASSWORD, "***")
    return value[:240] or exc.__class__.__name__
