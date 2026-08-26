from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import quote

import requests

from tender_parser.customer_contacts import (
    CustomerContactEnricher,
    CustomerEnrichmentReport,
)
from tender_parser.customers import (
    CUSTOMER_HEADERS,
    build_customer_registry,
    compact_tender_region,
)
from tender_parser.direct_links import documents_destination
from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, canonical_source_name


SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
VALUE_RANGE_CHUNK_ROWS = 200
VALUE_BATCH_MAX_BYTES = 1_500_000
LEGACY_DATA_HEADERS = [
    "Ключ",
    "Новая",
    "Состояние",
    "Приоритет",
    "Название",
    "Регион",
    "Сумма",
    "Срок подачи",
    "Дней до срока",
    "Заказчик",
    "Категория",
    "Источник",
    "Номер",
    "Статус площадки",
    "Впервые найдено",
    "Последний раз найдено",
    "Мой выбор",
    "Комментарий",
    "Ссылка",
    "Причина включения",
]
DATA_HEADERS = [
    *LEGACY_DATA_HEADERS[:12],
    "Официальный номер",
    *LEGACY_DATA_HEADERS[13:18],
    "Прямая ссылка",
    LEGACY_DATA_HEADERS[19],
    "Номер источника",
    "Ссылка источника",
    "Официальный источник",
    "Номер на площадке",
    "Ссылка на площадку",
    "Закон",
    "Способ определения",
    "Уверенность определения",
    "Документы",
]
DATA_LAST_COLUMN = "AC"
LEGACY_HEADER_ALIASES = {
    "Номер": "Номер источника",
    "Ссылка": "Ссылка источника",
}
PRIORITY_LABELS = {
    "hot": "Горячий",
    "review": "На проверку",
    "wide": "Широкий хвост",
    "excluded": "Отсеян",
}
SELECTED_CHOICES = {"Беру", "Думаю"}
TABLE_NAMES = {
    "Новые сегодня": "NewTodayTable",
    "Все актуальные": "ActiveTendersTable",
    "Мой отбор": "MySelectionTable",
    "Архив": "ArchiveTable",
}
SUMMARY_SHEET = "Сводка"
CUSTOMER_SHEET = "Потенциальные заказчики"
REGIONAL_SHEET = "Все региональные"
SOURCE_LABELS = {
    "etp-gpb": "ЭТП ГПБ",
    "roseltorg": "Росэлторг",
    "zakazrf": "ZakazRF",
    "sberbank-ast": "Сбербанк-АСТ",
    "tender-pro": "Tender.Pro",
    "torgi82": "Торги-82",
    "tektorg": "ТЭК-Торг",
    "crimea-small-purchases": "Малые закупки Крыма",
    "sevastopol-small-purchases": "Малые закупки Севастополя",
    "b2b-center": "B2B-Center",
    "eat-berezka": "ЕАТ «Берёзка»",
    "eis-zakupki": "ЕИС",
    "eis-regional-xml": "ЕИС XML (официальная выгрузка)",
    "rostender": "РосТендер",
    "rostender-resolution": "Первоисточники РосТендера",
    "rts-background-snapshot": "РТС — фоновый снимок",
    "rts-poisk": "РТС — поиск по всем площадкам",
    "rts-rosatom": "РТС — Росатом",
    "rts-zakupki-simferopol": "РТС — Симферополь",
    "rts-yalta-zmo": "РТС — Ялта ЗМО",
    "rts-market": "РТС — общий рынок",
    "EtpGpbApiSource": "ЭТП ГПБ",
    "RoseltorgSource": "Росэлторг",
    "ZakazRfSource": "ZakazRF",
    "SberbankAstSource": "Сбербанк-АСТ",
    "TenderProSource": "Tender.Pro",
    "Torgi82Source": "Торги-82",
    "TektorgSource": "ТЭК-Торг",
    "CrimeaSmallPurchasesSource": "Малые закупки Крыма",
    "SevastopolSmallPurchasesAdapter": "Малые закупки Севастополя",
    "B2BCenterSource": "B2B-Center",
    "EatIntegrationSource": "ЕАТ «Берёзка»",
    "EisZakupkiSource": "ЕИС",
    "EisRegionalXmlSource": "ЕИС XML (официальная выгрузка)",
    "RostenderSource": "РосТендер",
    "ImportFolderSource": "Локальный импорт",
}
SOURCE_URLS = {
    "etp-gpb": "https://etp.gpb.ru/",
    "EtpGpbApiSource": "https://etp.gpb.ru/",
    "roseltorg": "https://www.roseltorg.ru/",
    "RoseltorgSource": "https://www.roseltorg.ru/",
    "zakazrf": "https://zakazrf.ru/",
    "ZakazRfSource": "https://zakazrf.ru/",
    "sberbank-ast": "https://utp.sberbank-ast.ru/",
    "SberbankAstSource": "https://utp.sberbank-ast.ru/",
    "tender-pro": "https://www.tender.pro/",
    "TenderProSource": "https://www.tender.pro/",
    "torgi82": "https://torgi82.ru/",
    "tektorg": "https://www.tektorg.ru/",
    "Torgi82Source": "https://torgi82.ru/",
    "TektorgSource": "https://www.tektorg.ru/",
    "crimea-small-purchases": "https://zrk.rk.gov.ru/smallpurchases/",
    "CrimeaSmallPurchasesSource": "https://zrk.rk.gov.ru/smallpurchases/",
    "sevastopol-small-purchases": (
        "http://rks.sevzakaz.ru/zakupki-malogo-obema/oos-rks-001-001"
    ),
    "SevastopolSmallPurchasesAdapter": (
        "http://rks.sevzakaz.ru/zakupki-malogo-obema/oos-rks-001-001"
    ),
    "b2b-center": "https://www.b2b-center.ru/market/",
    "B2BCenterSource": "https://www.b2b-center.ru/market/",
    "eat-berezka": "https://agregatoreat.ru/",
    "EatIntegrationSource": "https://agregatoreat.ru/",
    "eis-zakupki": "https://zakupki.gov.ru/",
    "EisZakupkiSource": "https://zakupki.gov.ru/",
    "eis-regional-xml": "https://roskazna.gov.ru/gis/eis-zakupki-gov-ru",
    "EisRegionalXmlSource": "https://roskazna.gov.ru/gis/eis-zakupki-gov-ru",
    "rostender": "https://rostender.info/",
    "rostender-resolution": "https://rostender.info/",
    "RostenderSource": "https://rostender.info/",
    "rts-background-snapshot": "https://www.rts-tender.ru/",
    "rts-poisk": "https://www.rts-tender.ru/poisk/",
    "rts-rosatom": "https://www.rosatom.rts-tender.ru/market/",
    "rts-zakupki-simferopol": "https://zakupki-simferopol.rts-tender.ru/market/",
    "rts-yalta-zmo": "https://yalta-zmo.rts-tender.ru/market/",
    "rts-market": "https://www.rts-tender.ru/market/",
}
STATUS_LABELS = {
    "ok": "Работает",
    "empty": "Нет результатов",
    "partial": "Частично",
    "suspect_empty": "Подозрительно пусто",
    "skipped": "Пропущен",
    "blocked": "Заблокирован",
    "timeout": "Тайм-аут",
    "ssl_error": "Ошибка SSL",
    "error": "Ошибка",
}
UNHEALTHY_SOURCE_STATUSES = {
    "error",
    "blocked",
    "timeout",
    "ssl_error",
    "suspect_empty",
    "partial",
    "skipped",
}


@dataclass(frozen=True)
class GoogleSheetsConfig:
    enabled: bool = False
    spreadsheet_id: str = ""
    spreadsheet_url: str = ""
    service_account_file: Path | None = None
    timeout_seconds: int = 30
    customer_enrichment_enabled: bool = False
    customer_cache_file: Path | None = None
    customer_max_per_run: int = 25
    customer_timeout_seconds: int = 25

    @classmethod
    def from_env(cls, base_dir: Path) -> "GoogleSheetsConfig":
        raw_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        credentials_path = Path(raw_path) if raw_path else None
        if credentials_path is not None and not credentials_path.is_absolute():
            credentials_path = base_dir / credentials_path
        raw_customer_cache = os.getenv("CUSTOMER_CONTACTS_CACHE_PATH", "").strip()
        customer_cache = (
            Path(raw_customer_cache)
            if raw_customer_cache
            else base_dir / "data" / "customer_contacts.json"
        )
        if not customer_cache.is_absolute():
            customer_cache = base_dir / customer_cache
        enrichment_setting = os.getenv("CUSTOMER_CONTACTS_ENABLED", "").strip()
        return cls(
            enabled=_truthy(os.getenv("GOOGLE_SHEETS_ENABLED", "")),
            spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip(),
            spreadsheet_url=os.getenv("GOOGLE_SHEETS_URL", "").strip(),
            service_account_file=credentials_path,
            timeout_seconds=_positive_int(
                os.getenv("GOOGLE_SHEETS_TIMEOUT_SECONDS", ""), default=30
            ),
            customer_enrichment_enabled=(
                _truthy(enrichment_setting) if enrichment_setting else True
            ),
            customer_cache_file=customer_cache,
            customer_max_per_run=_positive_int(
                os.getenv("CUSTOMER_CONTACTS_MAX_PER_RUN", ""), default=25
            ),
            customer_timeout_seconds=_positive_int(
                os.getenv("CUSTOMER_CONTACTS_TIMEOUT_SECONDS", ""), default=25
            ),
        )


SyncStatus = Literal["disabled", "synced", "error"]


@dataclass(frozen=True)
class GoogleSheetsSyncResult:
    status: SyncStatus
    active_count: int = 0
    new_count: int = 0
    archived_count: int = 0
    detail: str = ""
    spreadsheet_url: str = ""


@dataclass(frozen=True)
class CustomerRegistrySyncResult:
    status: SyncStatus
    customer_count: int = 0
    rows_with_contacts: int = 0
    fetched: int = 0
    enriched: int = 0
    errors: int = 0
    detail: str = ""
    spreadsheet_url: str = ""


class GoogleSheetsRegistry:
    def __init__(
        self,
        config: GoogleSheetsConfig,
        session: requests.Session | object | None = None,
    ) -> None:
        self.config = config
        self._session = session
        self.last_customer_report = CustomerEnrichmentReport(0, 0, 0, 0, 0, 0)
        self.last_customer_rows: list[list[object]] = []

    def sync(
        self,
        current: list[TenderRecord],
        fresh: list[TenderRecord],
        source_result: SourceFetchResult,
        *,
        generated_at: datetime,
        profile: str,
        raw_count: int | None = None,
        unique_count: int | None = None,
        customer_candidates: list[TenderRecord] | None = None,
        regional_tenders: list[TenderRecord] | None = None,
    ) -> GoogleSheetsSyncResult:
        if not self.config.enabled:
            return GoogleSheetsSyncResult(
                status="disabled", spreadsheet_url=self.config.spreadsheet_url
            )
        if not self.config.spreadsheet_id:
            return self._error("не задан GOOGLE_SHEETS_SPREADSHEET_ID")
        try:
            session = self._session or self._authorized_session()
            metadata = self._metadata(session)
            self._ensure_data_columns(session, metadata)
            available_sheets = {
                str(sheet.get("properties", {}).get("title", ""))
                for sheet in metadata.get("sheets", [])
            }
            if REGIONAL_SHEET not in available_sheets:
                self._create_data_sheet(
                    session,
                    REGIONAL_SHEET,
                    row_count=max(1000, len(regional_tenders or []) + 10),
                )
                available_sheets.add(REGIONAL_SHEET)
            active_headers = _read_headers(
                self._get_values(
                    session,
                    f"'Все актуальные'!A1:{DATA_LAST_COLUMN}1",
                    value_render_option="FORMULA",
                )
            )
            archive_headers = _read_headers(
                self._get_values(
                    session,
                    f"'Архив'!A1:{DATA_LAST_COLUMN}1",
                    value_render_option="FORMULA",
                )
            )
            active_existing = [
                _migrate_existing_row(row, active_headers)
                for row in self._get_values(
                    session,
                    f"'Все актуальные'!A2:{DATA_LAST_COLUMN}1000",
                    value_render_option="FORMULA",
                )
            ]
            archive_existing = [
                _migrate_existing_row(row, archive_headers)
                for row in self._get_values(
                    session,
                    f"'Архив'!A2:{DATA_LAST_COLUMN}1000",
                    value_render_option="FORMULA",
                )
            ]
            history_existing = self._get_values(session, "'История запусков'!A2:L1000")
            customer_existing = (
                self._get_values(
                    session,
                    f"'{CUSTOMER_SHEET}'!A2:P",
                    value_render_option="FORMULA",
                )
                if CUSTOMER_SHEET in available_sheets
                else []
            )

            saved = _saved_fields([*active_existing, *archive_existing])
            fresh_keys = {tender.unique_key for tender in fresh}
            current_rows = [
                _record_row(tender, fresh_keys, saved, generated_at) for tender in current
            ]
            current_keys = {str(row[0]) for row in current_rows}
            unhealthy = {
                canonical_source_name(item.source): item
                for item in source_result.health
                if item.status in UNHEALTHY_SOURCE_STATUSES
            }

            # A temporary CAPTCHA, 429 or timeout must not make all tenders from
            # that source look closed. Keep its last-good rows until a healthy
            # run can confirm that they really disappeared.
            for existing in active_existing:
                row = _pad(existing, len(DATA_HEADERS))
                key = str(row[0] or "")
                source_id = _row_source_id(row[11])
                health = unhealthy.get(source_id)
                if not key or key in current_keys or health is None:
                    continue
                row[1] = ""
                row[2] = "⚠ Источник временно недоступен"
                row[19] = _source_warning(row[19], health.source, health.detail or health.status)
                current_rows.append(_decorate_existing_row(row))
                current_keys.add(key)

            archived_by_key = {
                str(row[0]): _pad(row, len(DATA_HEADERS))
                for row in archive_existing
                if row and row[0] and str(row[0]) not in current_keys
            }
            for existing in active_existing:
                row = _pad(existing, len(DATA_HEADERS))
                key = str(row[0] or "")
                if not key or key in current_keys:
                    continue
                row[1] = ""
                row[2] = "Не найдена в последнем запуске"
                archived_by_key[key] = row
            archive_rows = sorted(
                archived_by_key.values(), key=lambda row: str(row[15] or ""), reverse=True
            )
            archive_rows = [_decorate_existing_row(row) for row in archive_rows]

            current_by_key = {str(row[0]): row for row in current_rows}
            selection_rows = [
                row
                for row in [*current_by_key.values(), *archive_rows]
                if str(row[16] or "") in SELECTED_CHOICES
            ]
            fresh_rows = [row for row in current_rows if str(row[0]) in fresh_keys]

            history_rows = [row for row in history_existing if row and row[0]]
            history_stamp = _format_dt(generated_at)
            if not any(str(row[0]) == history_stamp for row in history_rows):
                history_rows.append(
                    _history_row(
                        source_result,
                        generated_at=generated_at,
                        profile=profile,
                        raw_count=raw_count,
                        unique_count=unique_count,
                        active_count=len(current_rows),
                        new_count=len(fresh_rows),
                    )
                )

            values_by_sheet = {
                "Новые сегодня": _with_row_formulas(
                    _rows_or_placeholder(fresh_rows, "Сегодня новых закупок нет", generated_at)
                ),
                "Все актуальные": _with_row_formulas(
                    _rows_or_placeholder(
                        current_rows, "Актуальных закупок пока нет", generated_at
                    )
                ),
                "Мой отбор": _with_row_formulas(
                    _rows_or_placeholder(
                        selection_rows, "Выбранных закупок пока нет", generated_at
                    )
                ),
                "Архив": _with_row_formulas(
                    _rows_or_placeholder(archive_rows, "Архив пока пуст", generated_at)
                ),
            }
            if regional_tenders is not None and REGIONAL_SHEET in available_sheets:
                regional_rows = [
                    _record_row(tender, fresh_keys, saved, generated_at)
                    for tender in regional_tenders
                ]
                values_by_sheet[REGIONAL_SHEET] = _with_row_formulas(
                    _rows_or_placeholder(
                        regional_rows,
                        "Региональных закупок в этом запуске нет",
                        generated_at,
                    )
                )
            customer_tenders = customer_candidates if customer_candidates is not None else current
            customer_rows = build_customer_registry(customer_tenders, customer_existing)
            customer_rows = self._enrich_customer_rows(
                customer_rows,
                customer_tenders,
                generated_at=generated_at,
            )
            self.last_customer_rows = customer_rows
            summary_rows = _summary_rows(
                source_result,
                generated_at=generated_at,
                profile=profile,
                raw_count=raw_count,
                unique_count=unique_count,
                active_count=len(current_rows),
                new_count=len(fresh_rows),
            )
            capacity_rows = dict(values_by_sheet)
            if CUSTOMER_SHEET in available_sheets:
                capacity_rows[CUSTOMER_SHEET] = customer_rows
            self._ensure_data_rows(session, metadata, capacity_rows)
            self._replace_values(
                session,
                values_by_sheet,
                history_rows,
                summary_rows=summary_rows if SUMMARY_SHEET in available_sheets else [],
                customer_rows=customer_rows if CUSTOMER_SHEET in available_sheets else [],
                available_sheets=available_sheets,
            )
            self._resize_tables(
                session,
                metadata,
                values_by_sheet,
                len(history_rows),
                customer_count=len(customer_rows),
            )
            self._compact_unused_rows(
                session,
                metadata,
                {
                    **{sheet: len(rows) + 1 for sheet, rows in values_by_sheet.items()},
                    "История запусков": len(history_rows) + 1,
                    SUMMARY_SHEET: len(summary_rows),
                    CUSTOMER_SHEET: len(customer_rows) + 1,
                },
            )
        except (OSError, ValueError, requests.RequestException) as exc:
            return self._error(exc.__class__.__name__)

        return GoogleSheetsSyncResult(
            status="synced",
            active_count=len(current_rows),
            new_count=len(fresh_rows),
            archived_count=len(archive_rows),
            detail=(
                "Google-реестр обновлён; "
                f"заказчиков {len(customer_rows)}, "
                f"с контактами {self.last_customer_report.rows_with_contacts}, "
                f"проверено ЕИС {self.last_customer_report.fetched}, "
                f"дополнено {self.last_customer_report.enriched}, "
                f"ошибок {self.last_customer_report.errors}"
            ),
            spreadsheet_url=self.config.spreadsheet_url,
        )

    def sync_customers(
        self,
        tenders: list[TenderRecord],
        *,
        generated_at: datetime,
        max_fetches: int | None = None,
    ) -> CustomerRegistrySyncResult:
        """Backfill only the customer CRM sheet without re-running tender sources."""

        if not self.config.enabled:
            return CustomerRegistrySyncResult(
                status="disabled", spreadsheet_url=self.config.spreadsheet_url
            )
        if not self.config.spreadsheet_id:
            return CustomerRegistrySyncResult(
                status="error",
                detail="не задан GOOGLE_SHEETS_SPREADSHEET_ID",
                spreadsheet_url=self.config.spreadsheet_url,
            )
        try:
            session = self._session or self._authorized_session()
            metadata = self._metadata(session)
            available_sheets = {
                str(sheet.get("properties", {}).get("title", ""))
                for sheet in metadata.get("sheets", [])
            }
            if CUSTOMER_SHEET not in available_sheets:
                raise ValueError(f"нет вкладки {CUSTOMER_SHEET}")
            existing = self._get_values(
                session,
                f"'{CUSTOMER_SHEET}'!A2:P",
                value_render_option="FORMULA",
            )
            rows = build_customer_registry(tenders, existing)
            rows = self._enrich_customer_rows(
                rows,
                tenders,
                generated_at=generated_at,
                max_fetches=max_fetches,
            )
            self._ensure_data_rows(session, metadata, {CUSTOMER_SHEET: rows})
            data = [
                {"range": f"'{CUSTOMER_SHEET}'!A1:P1", "values": [CUSTOMER_HEADERS]},
                {
                    "range": f"'{CUSTOMER_SHEET}'!A2:P{len(rows) + 1}",
                    "values": [_safe_customer_row(row) for row in rows],
                },
            ]
            response = session.post(  # type: ignore[attr-defined]
                f"{SHEETS_API}/{self.config.spreadsheet_id}/values:batchUpdate",
                json={"valueInputOption": "USER_ENTERED", "data": data},
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            clear_range = quote(
                f"'{CUSTOMER_SHEET}'!A{len(rows) + 2}:P", safe=""
            )
            response = session.post(  # type: ignore[attr-defined]
                f"{SHEETS_API}/{self.config.spreadsheet_id}/values/"
                f"{clear_range}:clear",
                json={},
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except (OSError, ValueError, requests.RequestException) as exc:
            return CustomerRegistrySyncResult(
                status="error",
                detail=exc.__class__.__name__,
                spreadsheet_url=self.config.spreadsheet_url,
            )
        report = self.last_customer_report
        return CustomerRegistrySyncResult(
            status="synced",
            customer_count=len(rows),
            rows_with_contacts=report.rows_with_contacts,
            fetched=report.fetched,
            enriched=report.enriched,
            errors=report.errors,
            detail="реестр заказчиков обновлён из истории и публичных карточек ЕИС",
            spreadsheet_url=self.config.spreadsheet_url,
        )

    def _enrich_customer_rows(
        self,
        rows: list[list[object]],
        tenders: list[TenderRecord],
        *,
        generated_at: datetime,
        max_fetches: int | None = None,
    ) -> list[list[object]]:
        if not self.config.customer_enrichment_enabled:
            self.last_customer_report = CustomerEnrichmentReport(
                len(rows),
                sum(any(str(value or "").strip() for value in row[4:11]) for row in rows),
                0,
                0,
                0,
                0,
            )
            return rows
        cache_path = self.config.customer_cache_file or Path("data/customer_contacts.json")
        enricher = CustomerContactEnricher(
            cache_path,
            timeout_seconds=self.config.customer_timeout_seconds,
            max_fetches=(
                max_fetches if max_fetches is not None else self.config.customer_max_per_run
            ),
            now=lambda: generated_at,
        )
        rows, self.last_customer_report = enricher.enrich(rows, tenders)
        return rows

    def _authorized_session(self) -> object:
        credentials_path = self.config.service_account_file
        if credentials_path is None or not credentials_path.is_file():
            raise OSError("service account file is missing")
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            str(credentials_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return AuthorizedSession(credentials)

    def _metadata(self, session: object) -> dict:
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}",
            params={"includeGridData": "false"},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid spreadsheet metadata")
        return payload

    def _create_data_sheet(
        self, session: object, title: str, *, row_count: int = 1000
    ) -> None:
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}:batchUpdate",
            json={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": title,
                                "gridProperties": {
                                    "rowCount": max(1000, row_count),
                                    "columnCount": len(DATA_HEADERS),
                                    "frozenRowCount": 1,
                                },
                                "tabColorStyle": {
                                    "rgbColor": {
                                        "red": 0.435,
                                        "green": 0.259,
                                        "blue": 0.757,
                                    }
                                },
                            }
                        }
                    }
                ]
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _ensure_data_rows(
        self,
        session: object,
        metadata: dict,
        values_by_sheet: dict[str, list[list[object]]],
    ) -> None:
        requests_payload: list[dict[str, object]] = []
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            title = str(properties.get("title", ""))
            sheet_id = properties.get("sheetId")
            row_count = properties.get("gridProperties", {}).get("rowCount")
            needed = len(values_by_sheet.get(title, [])) + 1
            if (
                title not in values_by_sheet
                or sheet_id is None
                or not isinstance(row_count, int)
                or row_count >= needed
            ):
                continue
            requests_payload.append(
                {
                    "appendDimension": {
                        "sheetId": int(sheet_id),
                        "dimension": "ROWS",
                        "length": needed - row_count,
                    }
                }
            )
        if not requests_payload:
            return
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}:batchUpdate",
            json={"requests": requests_payload},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _ensure_data_columns(self, session: object, metadata: dict) -> None:
        """Expand legacy 20-column grids before reading/writing through AB."""
        requests_payload: list[dict[str, object]] = []
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            title = str(properties.get("title", ""))
            sheet_id = properties.get("sheetId")
            column_count = properties.get("gridProperties", {}).get("columnCount")
            if (
                title not in {*TABLE_NAMES, REGIONAL_SHEET}
                or sheet_id is None
                or not isinstance(column_count, int)
                or column_count >= len(DATA_HEADERS)
            ):
                continue
            requests_payload.append(
                {
                    "appendDimension": {
                        "sheetId": int(sheet_id),
                        "dimension": "COLUMNS",
                        "length": len(DATA_HEADERS) - column_count,
                    }
                }
            )
        if not requests_payload:
            return
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}:batchUpdate",
            json={"requests": requests_payload},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _get_values(
        self,
        session: object,
        range_name: str,
        *,
        value_render_option: str = "UNFORMATTED_VALUE",
    ) -> list[list[object]]:
        response = session.get(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values/{quote(range_name, safe='')}",
            params={"valueRenderOption": value_render_option},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        values = payload.get("values", []) if isinstance(payload, dict) else []
        return values if isinstance(values, list) else []

    def _replace_values(
        self,
        session: object,
        values_by_sheet: dict[str, list[list[object]]],
        history_rows: list[list[object]],
        *,
        summary_rows: list[list[object]],
        customer_rows: list[list[object]],
        available_sheets: set[str],
    ) -> None:
        data: list[dict[str, object]] = []
        for sheet, rows in values_by_sheet.items():
            # Rewriting headers is intentional: old registries had 20 columns.
            # Named migration above preserves manual fields before the table is
            # expanded to the new provenance-aware 28-column layout.
            data.append(
                {
                    "range": f"'{sheet}'!A1:{DATA_LAST_COLUMN}1",
                    "values": [DATA_HEADERS],
                }
            )
            data.extend(
                _chunked_value_updates(
                    sheet,
                    rows,
                    last_column=DATA_LAST_COLUMN,
                    start_row=2,
                )
            )
        if history_rows:
            data.extend(
                _chunked_value_updates(
                    "История запусков",
                    history_rows,
                    last_column="L",
                    start_row=2,
                )
            )
        if summary_rows and SUMMARY_SHEET in available_sheets:
            data.extend(
                _chunked_value_updates(
                    SUMMARY_SHEET,
                    summary_rows,
                    last_column="H",
                    start_row=1,
                )
            )
        if customer_rows and CUSTOMER_SHEET in available_sheets:
            data.extend(
                _chunked_value_updates(
                    CUSTOMER_SHEET,
                    [_safe_customer_row(row) for row in customer_rows],
                    last_column="P",
                    start_row=2,
                )
            )
        self._post_value_batches(session, data)

        clear_specs = [
            (sheet, DATA_LAST_COLUMN, len(rows) + 2)
            for sheet, rows in values_by_sheet.items()
        ]
        clear_specs.append(("История запусков", "L", len(history_rows) + 2))
        if SUMMARY_SHEET in available_sheets:
            clear_specs.append((SUMMARY_SHEET, "H", len(summary_rows) + 1))
        if CUSTOMER_SHEET in available_sheets:
            clear_specs.append((CUSTOMER_SHEET, "P", len(customer_rows) + 2))
        for sheet, columns, first_unused_row in clear_specs:
            if first_unused_row > 1000 and sheet != CUSTOMER_SHEET:
                continue
            last_row = "" if sheet == CUSTOMER_SHEET else "1000"
            clear_range = quote(
                f"'{sheet}'!A{first_unused_row}:{columns}{last_row}", safe=""
            )
            response = session.post(  # type: ignore[attr-defined]
                f"{SHEETS_API}/{self.config.spreadsheet_id}/values/"
                f"{clear_range}:clear",
                json={},
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()

    def _post_value_batches(
        self, session: object, updates: list[dict[str, object]]
    ) -> None:
        batch: list[dict[str, object]] = []
        batch_bytes = 0
        for update in updates:
            update_bytes = len(
                json.dumps(update, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if batch and batch_bytes + update_bytes > VALUE_BATCH_MAX_BYTES:
                self._post_value_batch(session, batch)
                batch = []
                batch_bytes = 0
            batch.append(update)
            batch_bytes += update_bytes
        if batch:
            self._post_value_batch(session, batch)

    def _post_value_batch(
        self, session: object, updates: list[dict[str, object]]
    ) -> None:
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": updates},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _resize_tables(
        self,
        session: object,
        metadata: dict,
        values_by_sheet: dict[str, list[list[object]]],
        history_count: int,
        *,
        customer_count: int,
    ) -> None:
        located: dict[str, tuple[int, str]] = {}
        for sheet in metadata.get("sheets", []):
            title = str(sheet.get("properties", {}).get("title", ""))
            sheet_id = sheet.get("properties", {}).get("sheetId")
            for table in sheet.get("tables", []):
                if sheet_id is not None and table.get("tableId"):
                    located[str(table.get("name", ""))] = (int(sheet_id), str(table["tableId"]))

        requests_payload: list[dict[str, object]] = []
        for sheet, table_name in TABLE_NAMES.items():
            if table_name not in located:
                continue
            sheet_id, table_id = located[table_name]
            requests_payload.append(
                {
                    "updateTable": {
                        "table": {
                            "tableId": table_id,
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": len(values_by_sheet[sheet]) + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(DATA_HEADERS),
                            },
                        },
                        "fields": "range",
                    }
                }
            )
        if "RunHistoryTable" in located and history_count:
            sheet_id, table_id = located["RunHistoryTable"]
            requests_payload.append(
                {
                    "updateTable": {
                        "table": {
                            "tableId": table_id,
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": history_count + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": 12,
                            },
                        },
                        "fields": "range",
                    }
                }
            )
        if "PotentialCustomersTable" in located:
            sheet_id, table_id = located["PotentialCustomersTable"]
            requests_payload.append(
                {
                    "updateTable": {
                        "table": {
                            "tableId": table_id,
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": max(2, customer_count + 1),
                                "startColumnIndex": 0,
                                "endColumnIndex": len(CUSTOMER_HEADERS),
                            },
                        },
                        "fields": "range",
                    }
                }
            )
        if not requests_payload:
            return
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}:batchUpdate",
            json={"requests": requests_payload},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _compact_unused_rows(
        self,
        session: object,
        metadata: dict,
        used_rows: dict[str, int],
    ) -> None:
        requests_payload: list[dict[str, object]] = []
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            title = str(properties.get("title", ""))
            sheet_id = properties.get("sheetId")
            row_count = properties.get("gridProperties", {}).get("rowCount")
            if title not in used_rows or sheet_id is None or not isinstance(row_count, int):
                continue
            visible_end = max(1, min(used_rows[title], row_count))
            requests_payload.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": int(sheet_id),
                            "dimension": "ROWS",
                            "startIndex": 0,
                            "endIndex": visible_end,
                        },
                        "properties": {"hiddenByUser": False},
                        "fields": "hiddenByUser",
                    }
                }
            )
            if visible_end < row_count:
                requests_payload.append(
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": int(sheet_id),
                                "dimension": "ROWS",
                                "startIndex": visible_end,
                                "endIndex": row_count,
                            },
                            "properties": {"hiddenByUser": True},
                            "fields": "hiddenByUser",
                        }
                    }
                )
        if not requests_payload:
            return
        response = session.post(  # type: ignore[attr-defined]
            f"{SHEETS_API}/{self.config.spreadsheet_id}:batchUpdate",
            json={"requests": requests_payload},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def _error(self, detail: str) -> GoogleSheetsSyncResult:
        return GoogleSheetsSyncResult(
            status="error",
            detail=detail,
            spreadsheet_url=self.config.spreadsheet_url,
        )


def _record_row(
    tender: TenderRecord,
    fresh_keys: set[str],
    saved: dict[str, tuple[object, object, object]],
    generated_at: datetime,
) -> list[object]:
    first_seen, choice, comment = saved.get(
        tender.unique_key,
        (_format_dt(tender.discovered_at or generated_at), "Не выбрано", ""),
    )
    direct_number = tender.official_number
    direct_url = tender.official_url or tender.platform_url
    return [
        tender.unique_key,
        "🆕" if tender.unique_key in fresh_keys else "",
        "Актуальна",
        PRIORITY_LABELS.get(tender.review_priority or "", tender.review_priority or ""),
        tender.title,
        compact_tender_region(tender),
        tender.price,
        _format_dt(tender.deadline),
        "",
        tender.customer or "",
        tender.category or "",
        _source_link(tender.source),
        _identifier(direct_number, direct_url),
        tender.status or "",
        first_seen,
        _format_dt(generated_at),
        choice or "Не выбрано",
        comment or "",
        _hyperlink(direct_url, "Открыть первоисточник") if direct_url else "",
        tender.include_reason,
        _identifier(tender.tender_number, tender.url),
        _hyperlink(tender.url, "Открыть исходную карточку"),
        tender.official_source or "",
        _identifier(tender.platform_number, tender.platform_url),
        _hyperlink(tender.platform_url or "", "Открыть площадку")
        if tender.platform_url
        else "",
        tender.procurement_law or "",
        tender.resolution_method or "",
        tender.resolution_confidence,
        (
            _hyperlink(*documents_destination(tender))
            if documents_destination(tender)
            else ""
        ),
    ]


def _read_headers(values: list[list[object]]) -> list[str]:
    if not values:
        return []
    return [str(value or "").strip() for value in values[0]]


def _migrate_existing_row(raw: list[object], headers: list[str]) -> list[object]:
    """Map registry rows by header name, including the legacy 20-column layout.

    Manual columns must never move positionally when new provenance columns are
    introduced.  Legacy ``Номер``/``Ссылка`` describe the aggregator/source
    card; they are copied into explicit source columns and must never be shown
    as official data until resolution has actually succeeded.
    """
    source_headers = headers
    if not source_headers:
        source_headers = (
            LEGACY_DATA_HEADERS if len(raw) <= len(LEGACY_DATA_HEADERS) else DATA_HEADERS
        )

    aliases = {
        old.casefold(): new for old, new in LEGACY_HEADER_ALIASES.items()
    }
    current_by_name = {
        header.casefold(): index for index, header in enumerate(DATA_HEADERS)
    }
    result: list[object] = [""] * len(DATA_HEADERS)
    for index, value in enumerate(raw):
        if index >= len(source_headers):
            break
        source_header = source_headers[index].strip()
        if not source_header:
            continue
        target_header = aliases.get(source_header.casefold(), source_header)
        target_index = current_by_name.get(target_header.casefold())
        if target_index is not None:
            result[target_index] = value

    return result


def _saved_fields(rows: Iterable[list[object]]) -> dict[str, tuple[object, object, object]]:
    result: dict[str, tuple[object, object, object]] = {}
    for raw in rows:
        row = _pad(raw, len(DATA_HEADERS))
        key = str(row[0] or "")
        if key:
            result[key] = (row[14], row[16] or "Не выбрано", row[17] or "")
    return result


def _rows_or_placeholder(
    rows: list[list[object]], label: str, generated_at: datetime
) -> list[list[object]]:
    if rows:
        return rows
    result: list[object] = [""] * len(DATA_HEADERS)
    result[2] = "Нет данных"
    result[4] = label
    result[15] = _format_dt(generated_at)
    result[16] = "Не выбрано"
    return [result]


def _with_row_formulas(rows: list[list[object]]) -> list[list[object]]:
    result: list[list[object]] = []
    for row_number, raw in enumerate(rows, start=2):
        row = _pad(raw, len(DATA_HEADERS))
        if row[7]:
            row[8] = f'=IF(H{row_number}="";"";INT(H{row_number}-TODAY()))'
        else:
            row[8] = ""
        result.append(row)
    return result


def _decorate_existing_row(raw: list[object]) -> list[object]:
    row = _pad(raw, len(DATA_HEADERS))
    source = str(row[11] or "")
    if source and not source.startswith("="):
        row[11] = _source_link(source)
    link_specs = (
        (18, "Открыть первоисточник"),
        (21, "Открыть исходную карточку"),
        (24, "Открыть площадку"),
    )
    for index, label in link_specs:
        url = str(row[index] or "")
        if url.startswith(("http://", "https://")):
            row[index] = _hyperlink(url, label)
    return row


def _summary_rows(
    source_result: SourceFetchResult,
    *,
    generated_at: datetime,
    profile: str,
    raw_count: int | None,
    unique_count: int | None,
    active_count: int,
    new_count: int,
) -> list[list[object]]:
    bad_statuses = {"error", "blocked", "timeout", "ssl_error"}
    # Optional/feature-flagged sources may be intentionally skipped and remain
    # visible in the source table. They must not turn an otherwise successful
    # daily cycle into a permanent warning at the top of the dashboard.
    partial_statuses = {"partial", "suspect_empty"}
    has_errors = any(item.status in bad_statuses for item in source_result.health)
    has_partial = any(item.status in partial_statuses for item in source_result.health)
    overall = "Есть ошибки" if has_errors else "Частично" if has_partial else "Успешно"
    rows: list[list[object]] = [
        ["Мониторинг тендеров", "", "", "", "", "", "", ""],
        [
            "Последний запуск",
            _format_dt(generated_at),
            "Профиль",
            profile,
            "Итог",
            overall,
            "",
            "",
        ],
        [
            "Найдено",
            len(source_result.tenders) if raw_count is None else raw_count,
            "После дублей",
            (
                len({tender.unique_key for tender in source_result.tenders})
                if unique_count is None
                else unique_count
            ),
            "Актуальных",
            active_count,
            "Новых",
            new_count,
        ],
        ["", "", "", "", "", "", "", ""],
        ["Источник", "Статус", "Найдено", "Время, сек", "Примечание", "Площадка", "", ""],
    ]
    for item in source_result.health:
        label = SOURCE_LABELS.get(item.source, item.source)
        rows.append(
            [
                label,
                _health_status_label(item),
                item.found,
                item.elapsed_seconds,
                _health_detail(item),
                _hyperlink(SOURCE_URLS.get(item.source, ""), "Открыть")
                if SOURCE_URLS.get(item.source)
                else "",
                "",
                "",
            ]
        )
    return rows


def _health_status_label(item: object) -> str:
    source = str(getattr(item, "source", ""))
    status = str(getattr(item, "status", ""))
    if source == "ImportFolderSource" and status == "empty":
        return "Нет файлов"
    return STATUS_LABELS.get(status, status)


def _health_detail(item: object) -> str:
    source = str(getattr(item, "source", ""))
    detail = str(getattr(item, "detail", ""))
    if source == "ImportFolderSource" and "folder missing" in detail.casefold():
        return "Папка imports не создана — ручных выгрузок нет"
    return detail


def _source_link(source: str) -> object:
    label = SOURCE_LABELS.get(source, source)
    url = SOURCE_URLS.get(source, "")
    return _hyperlink(url, label) if url else label


def _row_source_id(value: object) -> str:
    """Extract the canonical source ID from plain text or a HYPERLINK formula."""
    text = str(value or "")
    folded = text.casefold()
    # More specific URLs/labels must win. Several sources share a parent host
    # (EIS HTML/XML and the RTS endpoints), so insertion order is insufficient.
    for source, url in sorted(
        SOURCE_URLS.items(), key=lambda item: len(item[1]), reverse=True
    ):
        if url.casefold() in folded:
            return canonical_source_name(source)
    for source, label in sorted(
        SOURCE_LABELS.items(), key=lambda item: len(item[1]), reverse=True
    ):
        if label.casefold() in folded:
            return canonical_source_name(source)
    return canonical_source_name(text)


def _source_warning(existing: object, source: str, detail: str) -> str:
    base = str(existing or "").split(" | ⚠ Источник временно недоступен", 1)[0]
    label = SOURCE_LABELS.get(source, source)
    warning = f"⚠ Источник временно недоступен ({label}): {detail}"
    return f"{base} | {warning}" if base else warning


def _hyperlink(url: str, label: str) -> object:
    if not url:
        return label
    escaped_url = url.replace('"', '""')
    escaped_label = label.replace('"', '""')
    return f'=HYPERLINK("{escaped_url}";"{escaped_label}")'


def _identifier(value: str | None, url: str | None = None) -> str:
    """Keep procurement identifiers exact and make them directly actionable.

    A leading apostrophe protects 19-digit EIS numbers from numeric rounding,
    but it is confusing in the formula bar and leaves the number itself
    unclickable.  A formula whose result is text preserves every digit without
    displaying an apostrophe.  When a source URL is known, the same cell is a
    hyperlink so the user can open the procurement by clicking its number.
    """
    if not value:
        return ""
    text = str(value)
    if url:
        return str(_hyperlink(url, text))
    escaped = text.replace('"', '""')
    return f'="{escaped}"'


def _safe_customer_row(row: list[object]) -> list[object]:
    """Keep contact text literal when values are written with USER_ENTERED.

    Phone numbers commonly start with ``+`` and would otherwise be parsed as
    formulas by Google Sheets. Only hyperlinks generated by this module are
    intentionally allowed to remain formulas in the customer registry.
    """
    result: list[object] = []
    for value in row:
        if not isinstance(value, str):
            result.append(value)
            continue
        stripped = value.lstrip()
        if stripped.upper().startswith("=HYPERLINK("):
            result.append(value)
        elif stripped.startswith(("=", "+", "-", "@")):
            result.append("'" + value)
        else:
            result.append(value)
    return result


def _history_row(
    source_result: SourceFetchResult,
    *,
    generated_at: datetime,
    profile: str,
    raw_count: int | None,
    unique_count: int | None,
    active_count: int,
    new_count: int,
) -> list[object]:
    good = sum(item.status in {"ok", "empty"} for item in source_result.health)
    partial = sum(item.status in {"partial", "suspect_empty"} for item in source_result.health)
    bad_items = [
        item
        for item in source_result.health
        if item.status in {"error", "blocked", "timeout", "ssl_error"}
    ]
    return [
        _format_dt(generated_at),
        profile,
        len(source_result.tenders) if raw_count is None else raw_count,
        (
            len({tender.unique_key for tender in source_result.tenders})
            if unique_count is None
            else unique_count
        ),
        active_count,
        new_count,
        len(source_result.health),
        good,
        partial,
        len(bad_items),
        "Есть ошибки" if bad_items else "Успешно",
        "; ".join(f"{item.source}: {item.detail or item.status}" for item in bad_items),
    ]


def _chunked_value_updates(
    sheet: str,
    rows: list[list[object]],
    *,
    last_column: str,
    start_row: int,
) -> list[dict[str, object]]:
    updates: list[dict[str, object]] = []
    for offset in range(0, len(rows), VALUE_RANGE_CHUNK_ROWS):
        chunk = rows[offset : offset + VALUE_RANGE_CHUNK_ROWS]
        first_row = start_row + offset
        last_row = first_row + len(chunk) - 1
        updates.append(
            {
                "range": f"'{sheet}'!A{first_row}:{last_column}{last_row}",
                "values": chunk,
            }
        )
    return updates


def _pad(row: list[object], length: int) -> list[object]:
    return [*row[:length], *([""] * max(0, length - len(row)))]


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


def _truthy(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on", "да"}


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
