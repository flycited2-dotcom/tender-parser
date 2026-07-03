from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from tender_parser.config import ETP_GPB_MAX_ERRORS, ETP_GPB_SEARCH_QUERIES, ETP_GPB_TIMEOUT_SECONDS
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError
from tender_parser.text import parse_price_rub


ETP_GPB_RSS_URL = "https://etpgpb.ru/procedures.rss"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.2"


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


class EtpGpbRssSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        timeout_seconds: int = ETP_GPB_TIMEOUT_SECONDS,
        max_errors: int = ETP_GPB_MAX_ERRORS,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or ETP_GPB_SEARCH_QUERIES
        self.timeout_seconds = timeout_seconds
        self.max_errors = max_errors

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        queries = self.queries or list(keywords)
        for query in queries:
            url = build_rss_url(query)
            try:
                response = get_with_retry(self.session, url, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                errors.append(f"{query}: {exc}")
                if len(errors) >= self.max_errors:
                    break
                continue

            for tender in parse_rss_feed(response.text, source_url=url):
                dedupe_key = tender.tender_number or tender.unique_key
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                collected.append(tender)

        if not collected and errors:
            raise SourceFetchError(f"ЭТП ГПБ RSS недоступен: {'; '.join(errors)}")
        return collected


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
