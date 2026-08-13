from __future__ import annotations

from datetime import datetime
from time import monotonic
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from tender_parser.config import HTTP_TIMEOUT_SECONDS
from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth
from tender_parser.sources.rts import SourceFetchError


CRIMEA_SMALL_PURCHASES_SOURCE = "crimea-small-purchases"
CRIMEA_SMALL_PURCHASES_GRID_URL = (
    "https://zrk.rk.gov.ru/smallpurchases/GzwSP/"
    "NoticesGrid?ItemId=87&show_title=on&expanded=1"
)
CRIMEA_SMALL_PURCHASES_API_URL = (
    "https://zrk.rk.gov.ru/smallpurchases/GzwSP/NoticesJson"
)
CRIMEA_SMALL_PURCHASES_DETAIL_URL = (
    "https://zrk.rk.gov.ru/smallpurchases/GzwSP/Notice?link={link}"
)
ACTIVE_STATUSES = ("Опубликовано", "Подача заявок")
MARKETPLACE_NAMES = {
    "1": "Портал малых закупок",
    "3": "РТС-тендер",
    "8": "Торговый портал ГПБ",
}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.5"


def build_notices_request(
    *,
    page: int = 0,
    page_size: int = 100,
    statuses: Iterable[str] = ACTIVE_STATUSES,
) -> dict[str, object]:
    """Build the public jqGrid request used by the official Crimea showcase."""
    normalized_statuses = [value.strip() for value in statuses if value.strip()]
    local_filter: list[dict[str, str]] = []
    if normalized_statuses:
        local_filter.append(
            {
                "columnname": "status",
                "columntype": "ListQuoted",
                "operation": "In",
                "value": ",".join(normalized_statuses),
                "name": "status_select",
            }
        )
    return {
        "settings": {
            "page": max(0, page),
            "sortField": "pub_date",
            "sortList": {},
            # The public controller silently caps larger values at 30. Keeping
            # the real server page size is essential for correct offsets.
            "rp": max(1, min(page_size, 30)),
            "rpList": [10, 20, 30],
            "totalRows": 0,
            "sortDir": "desc",
            "filter": {},
            "localFilter": local_filter,
        }
    }


def parse_notices_payload(payload: object) -> list[TenderRecord]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return []

    tenders: list[TenderRecord] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        number = _text(item.get("number"))
        title = _text(item.get("name"))
        link = _text(item.get("link"))
        if not number or not title or not link:
            continue

        marketplace_code = _text(item.get("marketplace"))
        marketplace_name = MARKETPLACE_NAMES.get(marketplace_code, "")
        external_url = _text(item.get("etp_url"))
        url = (
            external_url
            if external_url.startswith(("https://", "http://"))
            else CRIMEA_SMALL_PURCHASES_DETAIL_URL.format(link=link)
        )
        law = _text(item.get("reestr_type"))
        law_label = f"{law}-ФЗ" if law else ""
        status = _text(item.get("status"))
        okpd2 = _text(item.get("okpd2_codes"))
        customer = _text(item.get("uchr_sname"))
        raw_text = " ".join(
            value
            for value in (
                title,
                customer,
                number,
                status,
                law_label,
                okpd2,
                marketplace_name,
                "Республика Крым",
            )
            if value
        )
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source=CRIMEA_SMALL_PURCHASES_SOURCE,
                tender_number=number,
                customer=customer or None,
                region="Республика Крым",
                price=_parse_float(item.get("summa")),
                deadline=_parse_datetime(item.get("collecting_enddate")),
                status=" · ".join(
                    value for value in (status, law_label, marketplace_name) if value
                ),
                published_at=_parse_datetime(item.get("pub_date")),
                discovered_at=datetime.now(),
                raw_text=raw_text,
                delivery_region_evidence=(
                    "официальная региональная витрина закупок малого объема Республики Крым"
                ),
                source_confidence=0.95,
            )
        )
    return tenders


class CrimeaSmallPurchasesSource:
    """Active notices from the official public Crimea small-purchases showcase."""

    source_name = CRIMEA_SMALL_PURCHASES_SOURCE

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
        page_size: int = 100,
        max_pages: int = 5,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/html,*/*",
                "Referer": CRIMEA_SMALL_PURCHASES_GRID_URL,
            }
        )
        self.timeout_seconds = timeout_seconds
        self.page_size = max(1, min(page_size, 30))
        self.max_pages = max(1, max_pages)

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        result = self.fetch_with_report(keywords)
        if not result.tenders and result.errors:
            raise SourceFetchError(result.errors[0])
        return result.tenders

    def fetch_with_report(self, keywords: Iterable[str]) -> SourceFetchResult:
        del keywords  # Filtering and scoring are applied uniformly after source collection.
        started_at = monotonic()
        try:
            grid_response = self.session.get(
                CRIMEA_SMALL_PURCHASES_GRID_URL,
                timeout=self.timeout_seconds,
            )
            grid_response.raise_for_status()
            token = _request_verification_token(grid_response.text)
            if not token:
                raise SourceFetchError(
                    "Малые закупки Крыма изменили публичную форму: отсутствует защитный токен"
                )

            collected: list[TenderRecord] = []
            seen: set[str] = set()
            total_rows: int | None = None
            reached_page_limit = False
            for page in range(self.max_pages):
                response = self.session.post(
                    CRIMEA_SMALL_PURCHASES_API_URL,
                    json=build_notices_request(
                        page=page,
                        page_size=self.page_size,
                    ),
                    headers={
                        "RequestVerificationToken": token,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise SourceFetchError(
                        "Малые закупки Крыма вернули невалидный JSON"
                    ) from exc
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    raise SourceFetchError(
                        "Малые закупки Крыма вернули неожиданный формат данных"
                    )
                if isinstance(payload.get("totalRows"), int):
                    total_rows = payload["totalRows"]
                page_records = parse_notices_payload(payload)
                for tender in page_records:
                    if tender.unique_key in seen:
                        continue
                    seen.add(tender.unique_key)
                    collected.append(tender)

                if not payload["items"] or (
                    total_rows is not None and len(collected) >= total_rows
                ):
                    break
            else:
                reached_page_limit = total_rows is not None and len(collected) < total_rows

            status = "partial" if reached_page_limit else "ok" if collected else "empty"
            detail = ""
            errors: list[str] = []
            if reached_page_limit:
                detail = (
                    f"достигнут лимит {self.max_pages} страниц; "
                    f"прочитано {len(collected)} из {total_rows}"
                )
                errors.append(f"Малые закупки Крыма: {detail}")
            return SourceFetchResult(
                tenders=collected,
                health=[
                    SourceHealth(
                        source=self.source_name,
                        status=status,
                        found=len(collected),
                        elapsed_seconds=round(monotonic() - started_at, 3),
                        detail=detail,
                    )
                ],
                errors=errors,
            )
        except requests.Timeout as exc:
            return self._error_result(started_at, "timeout", f"тайм-аут: {exc}")
        except requests.exceptions.SSLError as exc:
            return self._error_result(started_at, "ssl_error", f"ошибка TLS: {exc}")
        except requests.RequestException as exc:
            return self._error_result(started_at, "error", f"сетевая ошибка: {exc}")
        except SourceFetchError as exc:
            return self._error_result(started_at, "error", str(exc))

    def _error_result(
        self,
        started_at: float,
        status: str,
        detail: str,
    ) -> SourceFetchResult:
        message = f"Малые закупки Крыма недоступны: {detail}"
        return SourceFetchResult(
            health=[
                SourceHealth(
                    source=self.source_name,
                    status=status,  # type: ignore[arg-type]
                    found=0,
                    elapsed_seconds=round(monotonic() - started_at, 3),
                    detail=message,
                )
            ],
            errors=[message],
        )


def _request_verification_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one('input[name="__RequestVerificationToken"]')
    return _text(node.get("value")) if node is not None else ""


def _text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _parse_float(value: object) -> float | None:
    text = _text(value).replace(" ", "").replace(",", ".")
    try:
        result = float(text)
    except ValueError:
        return None
    return result if result >= 0 else None


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
