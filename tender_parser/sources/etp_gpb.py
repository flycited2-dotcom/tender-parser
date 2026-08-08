from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from tender_parser.config import (
    ETP_GPB_ALLOWED_SECTIONS,
    ETP_GPB_MAX_ERRORS,
    ETP_GPB_MAX_PAGES_PER_QUERY,
    ETP_GPB_PAGE_SIZE,
    ETP_GPB_SEARCH_QUERIES,
    ETP_GPB_TIMEOUT_SECONDS,
)
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import parse_price_rub


ETP_GPB_RSS_URL = "https://etpgpb.ru/procedures.rss"
ETP_GPB_API_URL = "https://etpgpb.ru/api/v2/procedures/"
ETP_GPB_PUBLIC_URL = "https://etpgpb.ru"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.2"

SECTION_LABELS = {
    "Закупки.Бизнес223": "223-ФЗ",
    "Закупки.Гос44": "44-ФЗ",
    "Торговый портал": "Торговый портал",
}


def build_api_url(query: str, *, page: int = 1, page_size: int = ETP_GPB_PAGE_SIZE) -> str:
    params = {
        "page": page,
        "per": page_size,
        "search": query,
        "procedure[stage][0]": "accepting",
        "sort": "by_relevance",
    }
    return f"{ETP_GPB_API_URL}?{urlencode(params)}"


def parse_api_payload(
    payload: object,
    *,
    source_url: str,
    allowed_sections: Iterable[str] = ETP_GPB_ALLOWED_SECTIONS,
) -> list[TenderRecord]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []

    allowed = set(allowed_sections)
    tenders: list[TenderRecord] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            continue

        section = _as_text(attributes.get("section_category_name"))
        if section not in allowed:
            continue
        title = _as_text(attributes.get("title"))
        url = _procedure_url(attributes)
        if not title or not url:
            continue

        company = _as_text(attributes.get("company_name")) or None
        regions = attributes.get("lot_regions")
        region = (
            ", ".join(_as_text(value) for value in regions if _as_text(value))
            if isinstance(regions, list)
            else None
        )
        procedure_type = _as_text(attributes.get("procedure_type_name"))
        section_label = SECTION_LABELS.get(section, section)
        raw_text = " ".join(
            value
            for value in [title, company or "", region or "", section_label, procedure_type]
            if value
        )
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source="etp-gpb",
                tender_number=(
                    _as_text(attributes.get("registry_number"))
                    or _as_text(item.get("id"))
                    or None
                ),
                customer=company,
                region=region,
                price=_parse_api_price(attributes.get("amount")),
                deadline=_parse_iso_datetime(attributes.get("end_registration")),
                status=f"Подача заявок · {section_label}",
                published_at=_parse_iso_datetime(attributes.get("date_published")),
                discovered_at=datetime.now(),
                raw_text=raw_text or source_url,
            )
        )
    return tenders


def build_rss_url(query: str) -> str:
    params = {
        "procedure[category]": "actual",
        "procedure[name]": query,
    }
    return f"{ETP_GPB_RSS_URL}?{urlencode(params)}"


def parse_rss_feed(xml_text: str, source_url: str) -> list[TenderRecord]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    tenders: list[TenderRecord] = []
    for item in root.findall(".//item"):
        title = _node_text(item, "title")
        url = _node_text(item, "link") or _node_text(item, "guid")
        if not title or not url:
            continue

        description = _html_to_text(_node_text(item, "description"))
        raw_text = " ".join(part for part in [title, description] if part)
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source="etp-gpb",
                tender_number=_extract_number(description, url),
                customer=_extract_labeled_value(description, ["Организатор", "Заказчик"]),
                region=_extract_labeled_value(description, ["Регион поставки", "Регион"]),
                price=_extract_price(description),
                deadline=_extract_deadline(description),
                status="Актуально",
                published_at=_parse_pub_date(_node_text(item, "pubDate")),
                discovered_at=datetime.now(),
                raw_text=raw_text or source_url,
            )
        )
    return tenders


class EtpGpbApiSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        timeout_seconds: int = ETP_GPB_TIMEOUT_SECONDS,
        max_errors: int = ETP_GPB_MAX_ERRORS,
        page_size: int = ETP_GPB_PAGE_SIZE,
        max_pages_per_query: int = ETP_GPB_MAX_PAGES_PER_QUERY,
        allowed_sections: Iterable[str] = ETP_GPB_ALLOWED_SECTIONS,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.queries = queries or ETP_GPB_SEARCH_QUERIES
        self.timeout_seconds = timeout_seconds
        self.max_errors = max_errors
        self.page_size = page_size
        self.max_pages_per_query = max_pages_per_query
        self.allowed_sections = tuple(allowed_sections)

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        queries = self.queries or list(keywords)
        for query in queries:
            for page in range(1, self.max_pages_per_query + 1):
                url = build_api_url(query, page=page, page_size=self.page_size)
                try:
                    response = get_with_retry(self.session, url, timeout=self.timeout_seconds)
                    payload: Any = response.json()
                except (requests.RequestException, ValueError, TypeError) as exc:
                    errors.append(f"{query}: {exc}")
                    break

                tenders = parse_api_payload(
                    payload,
                    source_url=url,
                    allowed_sections=self.allowed_sections,
                )
                for tender in tenders:
                    dedupe_key = tender.tender_number or tender.unique_key
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    collected.append(tender)

                total_pages = _total_pages(payload)
                if page >= total_pages:
                    break

            if len(errors) >= self.max_errors:
                break

        if not collected and errors:
            raise SourceFetchError(f"ЭТП ГПБ API недоступен: {'; '.join(errors)}")
        return collected


# Совместимость для внешних импортов до перехода с отключённой RSS-ленты на JSON API.
EtpGpbRssSource = EtpGpbApiSource


def _total_pages(payload: object) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        return 1
    try:
        return max(1, int(payload["meta"].get("total_pages", 1)))
    except (TypeError, ValueError):
        return 1


def _procedure_url(attributes: dict[str, Any]) -> str:
    platform_url = _as_text(attributes.get("platform_url"))
    if platform_url.startswith(("http://", "https://")):
        return platform_url
    public_path = _as_text(attributes.get("rebranding_truncated_path")) or _as_text(
        attributes.get("truncated_path")
    )
    if public_path:
        return f"{ETP_GPB_PUBLIC_URL}/{public_path.lstrip('/')}"
    return ""


def _parse_api_price(value: object) -> float | None:
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: object) -> datetime | None:
    text = _as_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _as_text(value: object) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _node_text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)


def _extract_number(description: str, url: str) -> str | None:
    match = re.search(r"(?:номер\s+процедуры|№)\s*:?\s*([A-Za-zА-Яа-я0-9/-]+)", description, re.IGNORECASE)
    if match:
        return match.group(1)
    url_match = re.search(r"/(?:etp|tender)/(\d+)", url)
    if url_match:
        return url_match.group(1)
    return None


def _extract_labeled_value(description: str, labels: list[str]) -> str | None:
    for line in _description_lines(description):
        for label in labels:
            if line.lower().startswith(label.lower()):
                return line[len(label) :].lstrip(": ").strip(" .;")
    return None


def _extract_price(description: str) -> float | None:
    for line in _description_lines(description):
        if re.match(r"(начальная(?:\s+максимальная)?\s+цена(?:\s+договора)?|НМЦ)", line, re.IGNORECASE):
            return parse_price_rub(line.split(":", 1)[-1])
    return None


def _extract_deadline(description: str) -> datetime | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", description)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _parse_pub_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def _description_lines(description: str) -> list[str]:
    return [line.strip() for line in description.splitlines() if line.strip()]
