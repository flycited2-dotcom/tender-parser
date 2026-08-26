from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.rostender_resolution import procurement_law_for_number
from tender_parser.sources.eis import EIS_SOURCE_NAME, USER_AGENT


PLATFORM_SOURCE_NAMES = {
    "etp-gpb": "ЭТП ГПБ",
    "roseltorg": "Росэлторг",
    "zakazrf": "Заказ РФ",
    "sberbank-ast": "Сбербанк-АСТ",
    "rts-rosatom": "РТС-тендер",
    "rts-zakupki-simferopol": "РТС-тендер",
    "rts-yalta-zmo": "РТС-тендер",
    "rts-market": "РТС-тендер",
    "b2b-center": "B2B-Center",
    "eat-berezka": "ЕАТ «Берёзка»",
    "tender-pro": "Tender.Pro",
    "torgi82": "Торги-82",
    "tektorg": "ТЭК-Торг",
    "crimea-small-purchases": "Малые закупки Крыма",
}

MIXED_AGGREGATOR_SOURCES = {"rts-poisk"}


@dataclass(frozen=True)
class DirectLinkReport:
    checked: int = 0
    confirmed: int = 0
    invalidated: int = 0
    errors: tuple[str, ...] = ()


def normalize_direct_links(records: Iterable[TenderRecord]) -> list[TenderRecord]:
    """Expose source-native EIS/ETP links without losing provenance.

    The collector's ``tender_number`` and ``url`` always remain the source
    identity.  This function only fills the user-facing direct fields when the
    source itself is authoritative for that destination.
    """

    normalized: list[TenderRecord] = []
    for record in records:
        number = (record.tender_number or "").strip()
        destination_host = (urlparse(record.url).hostname or "").casefold()
        # RTS "Поиск закупок" is a mixed catalogue: each result already
        # exposes the exact public destination in EIS or on a trading platform.
        if (
            record.source in MIXED_AGGREGATOR_SOURCES
            and destination_host == "zakupki.gov.ru"
            and procurement_law_for_number(number)
        ):
            normalized.append(
                replace(
                    record,
                    official_number=record.official_number or number,
                    official_url=record.official_url or record.url,
                    official_source=record.official_source or EIS_SOURCE_NAME,
                    procurement_law=(
                        record.procurement_law or procurement_law_for_number(number)
                    ),
                    resolution_method=record.resolution_method or "rts-poisk-direct-eis",
                    resolution_confidence=max(record.resolution_confidence, 1.0),
                )
            )
            continue

        if record.source in MIXED_AGGREGATOR_SOURCES:
            normalized.append(
                replace(
                    record,
                    platform_number=record.platform_number or record.tender_number,
                    platform_url=record.platform_url or record.url,
                    procurement_law=(
                        record.procurement_law or procurement_law_for_number(number)
                    ),
                    resolution_method=record.resolution_method or "rts-poisk-direct-platform",
                    resolution_confidence=max(record.resolution_confidence, 1.0),
                )
            )
            continue

        if record.source == EIS_SOURCE_NAME and procurement_law_for_number(number):
            normalized.append(
                replace(
                    record,
                    official_number=record.official_number or number,
                    official_url=record.official_url or record.url,
                    official_source=record.official_source or EIS_SOURCE_NAME,
                    procurement_law=(
                        record.procurement_law or procurement_law_for_number(number)
                    ),
                    resolution_method=record.resolution_method or "source-native",
                    resolution_confidence=max(record.resolution_confidence, 1.0),
                )
            )
            continue

        if record.source in PLATFORM_SOURCE_NAMES:
            normalized.append(
                replace(
                    record,
                    platform_number=record.platform_number or record.tender_number,
                    platform_url=record.platform_url or record.url,
                    procurement_law=(
                        record.procurement_law or procurement_law_for_number(number)
                    ),
                    resolution_method=record.resolution_method or "source-native",
                    resolution_confidence=max(record.resolution_confidence, 1.0),
                )
            )
            continue
        normalized.append(record)
    return normalized


class EisCardLinkEnricher:
    """Verify EIS destinations and discover the actual trading platform.

    A number merely printed by an aggregator is not promoted to an official
    link until the public EIS card itself confirms it.  Native EIS rows are
    also enriched with the platform address shown in their official card.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: int = 15,
        max_records: int = 100,
    ) -> None:
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout_seconds = timeout_seconds
        self.max_records = max(0, max_records)
        self.last_report = DirectLinkReport()

    def enrich(self, records: Iterable[TenderRecord]) -> list[TenderRecord]:
        result: list[TenderRecord] = []
        checked = confirmed = invalidated = 0
        errors: list[str] = []
        for record in normalize_direct_links(records):
            if record.review_priority == "excluded" or checked >= self.max_records:
                result.append(record)
                continue
            candidate_url = _eis_candidate_url(record)
            candidate_number = (record.official_number or record.tender_number or "").strip()
            if not candidate_url or procurement_law_for_number(candidate_number) is None:
                result.append(record)
                continue
            checked += 1
            try:
                response = self.session.get(candidate_url, timeout=self.timeout_seconds)
                response.raise_for_status()
            except requests.RequestException as exc:
                # A network failure must not erase a previously verified link.
                errors.append(f"{candidate_number}: {exc.__class__.__name__}")
                result.append(record)
                continue

            page = parse_eis_card_links(response.text, response.url, candidate_number)
            if not page.confirmed:
                if record.source == "rostender":
                    invalidated += 1
                    result.append(
                        replace(
                            record,
                            official_number=None,
                            official_url=None,
                            official_source=None,
                            platform_number=record.platform_number or candidate_number,
                            platform_url=None,
                            resolution_method="rostender-meta-unverified",
                            resolution_confidence=min(record.resolution_confidence, 0.6),
                        )
                    )
                else:
                    result.append(record)
                continue

            confirmed += 1
            platform_url = record.platform_url
            platform_number = record.platform_number
            if page.platform_url:
                platform_url = build_platform_destination(
                    page.platform_name, page.platform_url, candidate_number,
                    procurement_law=record.procurement_law
                    or procurement_law_for_number(candidate_number),
                )
                platform_number = platform_number or candidate_number
            result.append(
                replace(
                    record,
                    official_number=candidate_number,
                    official_url=response.url,
                    official_source=EIS_SOURCE_NAME,
                    platform_number=platform_number,
                    platform_url=platform_url,
                    procurement_law=(
                        record.procurement_law
                        or procurement_law_for_number(candidate_number)
                    ),
                    resolution_method=(
                        "source-native+eis-verified"
                        if record.source == EIS_SOURCE_NAME
                        else "rostender-meta+eis-verified"
                    ),
                    resolution_confidence=1.0,
                )
            )

        self.last_report = DirectLinkReport(
            checked=checked,
            confirmed=confirmed,
            invalidated=invalidated,
            errors=tuple(errors),
        )
        return result


@dataclass(frozen=True)
class EisCardLinks:
    confirmed: bool
    documents_url: str | None = None
    platform_name: str | None = None
    platform_url: str | None = None


def parse_eis_card_links(html: str, source_url: str, number: str) -> EisCardLinks:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    not_found = "запрашиваемая страница не существует" in text.casefold()
    confirmed = not not_found and bool(
        re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text)
    )
    if not confirmed:
        return EisCardLinks(confirmed=False)

    documents_url = None
    for anchor in soup.select("a[href]"):
        label = " ".join(anchor.get_text(" ", strip=True).split()).casefold()
        href = urljoin(source_url, str(anchor.get("href") or ""))
        if label == "документы" and "zakupki.gov.ru" in href:
            documents_url = href
            break

    platform_name = None
    platform_url = None
    match = re.search(
        r"Наименование электронной площадки.*?«Интернет»\s+(.+?)\s+"
        r"Адрес электронной площадки.*?«Интернет»\s+(https?://[^\s]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        platform_name = match.group(1).strip()
        platform_url = match.group(2).rstrip(".,;)")
    return EisCardLinks(
        confirmed=True,
        documents_url=documents_url,
        platform_name=platform_name,
        platform_url=platform_url,
    )


def build_platform_destination(
    platform_name: str | None,
    platform_url: str,
    number: str,
    *,
    procurement_law: str | None,
) -> str:
    """Return an exact platform card where its public URL contract is known."""

    identity = f"{platform_name or ''} {platform_url}".casefold()
    if "rts-tender" in identity or "ртс-тендер" in identity:
        if procurement_law == "44-ФЗ":
            return (
                "https://www.rts-tender.ru/auctionsearch/ctl/procDetail/"
                f"mid/691/number/{number}/etpName/fks"
            )
        return "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx"
    return platform_url.replace("http://", "https://", 1)


def documents_destination(record: TenderRecord) -> tuple[str, str] | None:
    """Return a one-click documents destination suitable for reports."""

    url = record.official_url or (
        record.url if record.source == EIS_SOURCE_NAME else None
    )
    if not url or urlparse(url).hostname != "zakupki.gov.ru":
        return None
    if record.procurement_law == "44-ФЗ" or "/notice/ea20/" in url:
        path = re.sub(r"/view/[^/?]+\.html$", "/view/documents.html", urlparse(url).path)
        if path != urlparse(url).path:
            parsed = urlparse(url)
            return parsed._replace(path=path, fragment="").geturl(), "Открыть документы"
    return url, "Документы в карточке"


def _eis_candidate_url(record: TenderRecord) -> str | None:
    if record.official_url and urlparse(record.official_url).hostname == "zakupki.gov.ru":
        return record.official_url
    if record.source == EIS_SOURCE_NAME and urlparse(record.url).hostname == "zakupki.gov.ru":
        return record.url
    return None
