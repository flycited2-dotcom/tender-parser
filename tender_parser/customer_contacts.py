from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from typing import Callable, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from tender_parser.customers import clean_customer_name, organization_key
from tender_parser.http import get_with_retry
from tender_parser.models import TenderRecord
from tender_parser.sources.eis import build_search_url as build_eis_search_url
from tender_parser.text import normalize_text


EIS_HOSTS = {"zakupki.gov.ru", "www.zakupki.gov.ru"}
DEFAULT_SUCCESS_TTL_DAYS = 30
DEFAULT_RETRY_TTL_HOURS = 24
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.3"
PARSER_VERSION = 3


@dataclass(frozen=True)
class CustomerContact:
    inn: str = ""
    legal_address: str = ""
    postal_address: str = ""
    email: str = ""
    phone: str = ""
    contact_person: str = ""
    website: str = ""
    source_url: str = ""

    @property
    def has_data(self) -> bool:
        return any(
            (
                self.inn,
                self.legal_address,
                self.postal_address,
                self.email,
                self.phone,
                self.contact_person,
                self.website,
            )
        )


@dataclass(frozen=True)
class CustomerEnrichmentReport:
    total_rows: int
    rows_with_contacts: int
    fetched: int
    enriched: int
    cached: int
    errors: int


def parse_eis_contact_page(html: str, source_url: str) -> CustomerContact:
    """Extract only explicitly labelled public organization/contact fields."""

    soup = BeautifulSoup(html, "html.parser")
    values: dict[str, str] = {}

    for section in soup.select(".blockInfo__section"):
        title = _text(section.select_one(".section__title"))
        value = _text(section.select_one(".section__info"))
        if title and value:
            values.setdefault(normalize_text(title), value)

    for title_node in soup.select(".registry-entry__body-title"):
        title = _text(title_node)
        parent = title_node.parent
        value_node = parent.select_one(".registry-entry__body-value") if parent else None
        value = _text(value_node)
        if title and value:
            values.setdefault(normalize_text(title), value)

    def first(*labels: str) -> str:
        for label in labels:
            value = values.get(normalize_text(label), "")
            if value and normalize_text(value) != "информация отсутствует":
                return value
        return ""

    href_inn = ""
    for link in soup.select('a[href*="/epz/organization/view223/info.html"]'):
        raw_inn = (parse_qs(urlparse(str(link.get("href", ""))).query).get("inn") or [""])[0]
        href_inn = _digits(raw_inn, lengths={10, 12})
        if href_inn:
            break

    return CustomerContact(
        inn=_digits(first("ИНН"), lengths={10, 12}) or href_inn,
        legal_address=first("Место нахождения", "Местонахождение"),
        postal_address=first("Почтовый адрес"),
        email=first("Контактный адрес электронной почты", "Адрес электронной почты"),
        phone=first("Номер контактного телефона", "Телефон"),
        contact_person=first(
            "Контактное лицо",
            "Ответственное должностное лицо",
            "Контактное лицо / должность",
        ),
        website=first(
            "Адрес организации в сети Интернет",
            "Официальный сайт",
            "Адрес сайта",
        ),
        source_url=source_url,
    )


def find_eis_organization_url(html: str, source_url: str, customer_name: str = "") -> str:
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select('a[href*="/epz/organization/view/info.html?organizationCode="]')
    if not links:
        return ""
    normalized_customer = normalize_text(customer_name)
    if normalized_customer:
        for link in links:
            link_name = normalize_text(_text(link))
            if link_name and (link_name in normalized_customer or normalized_customer in link_name):
                return urljoin(source_url, str(link.get("href", "")))
    return urljoin(source_url, str(links[0].get("href", "")))


class CustomerContactEnricher:
    """Fill missing CRM fields from public EIS pages and retain a local cache."""

    def __init__(
        self,
        cache_path: Path,
        *,
        session: requests.Session | None = None,
        timeout_seconds: int = 25,
        max_fetches: int = 25,
        min_interval_seconds: float = 0.2,
        sleeper: Callable[[float], None] = sleep,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.cache_path = cache_path
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_fetches = max(0, max_fetches)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.sleeper = sleeper
        self.now = now

    def enrich(
        self,
        rows: list[list[object]],
        tenders: Iterable[TenderRecord],
    ) -> tuple[list[list[object]], CustomerEnrichmentReport]:
        cache = self._load_cache()
        candidates = _best_tender_by_customer(tenders)
        fetched = 0
        enriched = 0
        cached = 0
        errors = 0
        changed = False

        work_rows = sorted(
            rows,
            key=lambda row: self._fetch_priority(
                cache.get(str(row[0] or ""), {}) if row else {},
                _eis_notice_url(candidates.get(str(row[0] or ""))) if row else "",
            ),
        )
        for row in work_rows:
            if len(row) < 16 or not str(row[0] or "").strip():
                continue
            key = str(row[0])
            tender = candidates.get(key)
            source_url = _eis_notice_url(tender) if tender else ""
            entry = cache.get(key, {})
            contact = _contact_from_entry(entry)

            # Versions before 0.3 briefly wrote an EIS source URL even when the
            # page contained no usable contact fields. Remove only that known
            # generated artefact; never clear a manually entered source.
            if (
                str(entry.get("status", "")) == "empty"
                and not any(str(value or "").strip() for value in row[4:11])
                and str(row[11] or "") == contact.source_url
            ):
                row[11] = ""
                row[13] = ""

            if contact.has_data:
                before = tuple(row[4:11])
                _apply_contact(row, contact)
                if tuple(row[4:11]) != before:
                    cached += 1

            needs_fetch = self._needs_fetch(entry, source_url)
            if not needs_fetch or fetched >= self.max_fetches or not source_url:
                continue

            fetched += 1
            if fetched > 1 and self.min_interval_seconds:
                self.sleeper(self.min_interval_seconds)
            try:
                contact = self._fetch_contact(source_url, str(row[1] or ""))
            except requests.RequestException as exc:
                errors += 1
                cache[key] = {
                    "status": "error",
                    "checked_at": self.now().isoformat(timespec="seconds"),
                    "source_url": source_url,
                    "parser_version": PARSER_VERSION,
                    "detail": exc.__class__.__name__,
                }
                changed = True
                continue

            before = tuple(row[4:11])
            _apply_contact(row, contact)
            if tuple(row[4:11]) != before:
                enriched += 1
            cache[key] = {
                "status": "ok" if contact.has_data else "empty",
                "checked_at": self.now().isoformat(timespec="seconds"),
                "source_url": source_url,
                "parser_version": PARSER_VERSION,
                "contact": asdict(contact),
            }
            changed = True

        if changed:
            self._save_cache(cache)
        rows_with_contacts = sum(
            any(str(value or "").strip() for value in row[4:11]) for row in rows
        )
        return rows, CustomerEnrichmentReport(
            total_rows=len(rows),
            rows_with_contacts=rows_with_contacts,
            fetched=fetched,
            enriched=enriched,
            cached=cached,
            errors=errors,
        )

    def _fetch_contact(self, notice_url: str, customer_name: str) -> CustomerContact:
        try:
            response = get_with_retry(
                self.session,
                notice_url,
                timeout=self.timeout_seconds,
                retries=1,
                backoff_seconds=0.5,
            )
        except requests.HTTPError:
            # A platform/aggregator can expose the correct registry number but
            # an obsolete EIS card subtype (ea20/zk20/notice223). Resolve the
            # number through official EIS search instead of abandoning the CRM
            # contact.
            number = _procurement_number_from_url(notice_url)
            if not number:
                raise
            notice_url = build_eis_search_url(number)
            response = get_with_retry(
                self.session,
                notice_url,
                timeout=self.timeout_seconds,
                retries=1,
                backoff_seconds=0.5,
            )
        resolved_notice_url = _notice_url_from_search(response.text, notice_url)
        if resolved_notice_url and resolved_notice_url != notice_url:
            response = get_with_retry(
                self.session,
                resolved_notice_url,
                timeout=self.timeout_seconds,
                retries=1,
                backoff_seconds=0.5,
            )
            notice_url = resolved_notice_url
        notice_contact = parse_eis_contact_page(response.text, notice_url)
        organization_url = find_eis_organization_url(
            response.text, notice_url, customer_name=customer_name
        )
        if not organization_url or not _is_eis_url(organization_url):
            return notice_contact

        response = get_with_retry(
            self.session,
            organization_url,
            timeout=self.timeout_seconds,
            retries=1,
            backoff_seconds=0.5,
        )
        organization_contact = parse_eis_contact_page(response.text, organization_url)
        return _merge_contacts(organization_contact, notice_contact)

    def _needs_fetch(self, entry: dict[str, object], source_url: str) -> bool:
        if not source_url:
            return False
        if str(entry.get("source_url", "")) != source_url:
            return True
        checked_at = _parse_dt(str(entry.get("checked_at", "")))
        if checked_at is None:
            return True
        status = str(entry.get("status", ""))
        if status != "ok" and str(entry.get("parser_version", "")) != str(PARSER_VERSION):
            return True
        ttl = (
            timedelta(days=DEFAULT_SUCCESS_TTL_DAYS)
            if status == "ok"
            else timedelta(hours=DEFAULT_RETRY_TTL_HOURS)
        )
        return self.now() - checked_at >= ttl

    @staticmethod
    def _fetch_priority(entry: dict[str, object], source_url: str) -> int:
        """Prevent old empty pages from starving newly discovered customers."""

        if not source_url:
            return 9
        if not entry or str(entry.get("source_url", "")) != source_url:
            return 0
        status = str(entry.get("status", ""))
        if status == "error":
            return 1
        if status == "empty":
            return 2
        if status == "ok":
            return 3
        return 1

    def _load_cache(self) -> dict[str, dict[str, object]]:
        if not self.cache_path.is_file():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        return entries if isinstance(entries, dict) else {}

    def _save_cache(self, entries: dict[str, dict[str, object]]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": entries}
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.cache_path)


def _best_tender_by_customer(tenders: Iterable[TenderRecord]) -> dict[str, TenderRecord]:
    result: dict[str, TenderRecord] = {}
    for tender in tenders:
        if not tender.customer:
            continue
        key = organization_key(clean_customer_name(tender.customer))
        if not key:
            continue
        current = result.get(key)
        if current is None or _tender_contact_score(tender) > _tender_contact_score(current):
            result[key] = tender
    return result


def _tender_contact_score(tender: TenderRecord) -> tuple[int, datetime]:
    return (
        int(bool(_eis_notice_url(tender))),
        tender.discovered_at or datetime.min,
    )


def _eis_notice_url(tender: TenderRecord | None) -> str:
    if tender is None:
        return ""
    for value in (tender.official_url, tender.url):
        if not value or not _is_eis_url(value):
            continue
        path = urlparse(value).path.casefold()
        if "/order/notice/" in path or "/purchase/public/purchase/info/" in path:
            return value
    number = (tender.official_number or tender.tender_number or "").strip()
    if re.fullmatch(r"(?:\d{19}|3\d{10})", number):
        return build_eis_search_url(number)
    return ""


def _is_eis_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").casefold() in EIS_HOSTS


def _notice_url_from_search(html: str, source_url: str) -> str:
    parsed = urlparse(source_url)
    if "/extendedsearch/results" not in parsed.path.casefold():
        return source_url
    number = (parse_qs(parsed.query).get("searchString") or [""])[0]
    if not number:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select("a[href]"):
        href = str(link.get("href", ""))
        candidate = urljoin(source_url, href)
        candidate_path = urlparse(candidate).path.casefold()
        if number not in candidate and number not in _text(link):
            continue
        if "/order/notice/" in candidate_path or "/purchase/public/purchase/info/" in candidate_path:
            return candidate
    return ""


def _procurement_number_from_url(value: str) -> str:
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    for name in ("regNumber", "searchString"):
        number = (query.get(name) or [""])[0].strip()
        if re.fullmatch(r"(?:\d{19}|3\d{10})", number):
            return number
    return ""


def _apply_contact(row: list[object], contact: CustomerContact) -> None:
    if not contact.has_data:
        return
    values = [
        contact.inn,
        contact.legal_address,
        contact.postal_address,
        contact.email,
        contact.phone,
        contact.contact_person,
        contact.website,
        contact.source_url,
    ]
    changed = False
    for offset, value in enumerate(values, start=4):
        if value and not str(row[offset] or "").strip():
            row[offset] = value
            changed = True
    if changed and not str(row[13] or "").strip():
        row[13] = datetime.now().strftime("%d.%m.%Y")


def _merge_contacts(primary: CustomerContact, fallback: CustomerContact) -> CustomerContact:
    return CustomerContact(
        inn=primary.inn or fallback.inn,
        legal_address=primary.legal_address or fallback.legal_address,
        postal_address=primary.postal_address or fallback.postal_address,
        email=primary.email or fallback.email,
        phone=primary.phone or fallback.phone,
        contact_person=primary.contact_person or fallback.contact_person,
        website=primary.website or fallback.website,
        source_url=primary.source_url or fallback.source_url,
    )


def _contact_from_entry(entry: dict[str, object]) -> CustomerContact:
    raw = entry.get("contact", {})
    if not isinstance(raw, dict):
        return CustomerContact()
    allowed = {field: str(raw.get(field, "") or "") for field in CustomerContact.__dataclass_fields__}
    return CustomerContact(**allowed)


def _digits(value: str, *, lengths: set[int]) -> str:
    result = "".join(character for character in value if character.isdigit())
    return result if len(result) in lengths else ""


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())  # type: ignore[attr-defined]
