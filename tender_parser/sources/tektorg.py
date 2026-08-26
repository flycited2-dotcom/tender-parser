from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Callable, Iterable
from urllib.parse import urlencode

import requests

from tender_parser.models import TenderRecord
from tender_parser.regions import detect_region
from tender_parser.sources.rts import SourceFetchError


TEKTORG_SOAP_URL = "https://api.tektorg.ru/procedures/soap"
TEKTORG_SOURCE_NAME = "tektorg"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.4"

# The parent ``zakupki`` section includes its nested Rosneft/Rostec/Geotech
# sections.  The remaining codes are separate procurement sections listed by
# the official TEK-Torg API.  Sales-only sections are intentionally excluded.
DEFAULT_SECTION_CODES = (
    "44fz",
    "zakupki",
    "market",
    "interrao",
    "nornikel",
)

SECTION_LABELS = {
    "44fz": "44-ФЗ",
    "zakupki": "Закупки 223-ФЗ и коммерческие",
    "market": "Интернет-магазин",
    "interrao": "Интер РАО",
    "oboronenergo": "Оборонэнерго",
    "rusgazburenie": "РусГазБурение",
    "rosmorport_kim": "Росморпорт",
    "portone_im": "Порт One",
    "nornikel": "Норникель",
    "ncm": "Норильский обеспечивающий комплекс",
    "unipro": "Юнипро",
    "znft": "Зарубежнефть",
    "irkutskoil": "ИНК",
}

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
SOAP_API = "https://api.tektorg.ru/procedures/soap"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSD = "http://www.w3.org/2001/XMLSchema"


def build_soap_request(
    *,
    section_code: str,
    page: int,
    page_size: int,
    start_update_at: datetime,
    end_update_at: datetime,
) -> bytes:
    """Build the documented anonymous TEK-Torg RPC request safely."""

    ET.register_namespace("SOAP-ENV", SOAP_ENV)
    ET.register_namespace("tek", SOAP_API)
    ET.register_namespace("xsi", XSI)
    ET.register_namespace("xsd", XSD)
    envelope = ET.Element(f"{{{SOAP_ENV}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_ENV}}}Body")
    operation = ET.SubElement(body, f"{{{SOAP_API}}}procedures")
    symbol = ET.SubElement(
        operation,
        "symbol",
        {f"{{{XSI}}}type": "tek:exportRequestType"},
    )
    _typed_node(symbol, "startUpdateAt", "xsd:dateTime", _soap_datetime(start_update_at))
    _typed_node(symbol, "endUpdateAt", "xsd:dateTime", _soap_datetime(end_update_at))
    _typed_node(symbol, "sectionCode", "xsd:string", section_code)
    _typed_node(symbol, "limitPage", "xsd:int", str(max(1, page_size)))
    _typed_node(symbol, "page", "xsd:int", str(max(1, page)))
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def parse_soap_response(
    xml_text: str | bytes,
    *,
    source_url: str = TEKTORG_SOAP_URL,
    section_code: str = "",
) -> tuple[list[TenderRecord], int]:
    """Parse one TEK-Torg response and return records plus total pages."""

    if isinstance(xml_text, bytes):
        safe_text = xml_text.decode("utf-8", errors="replace")
    else:
        safe_text = xml_text
    if "<!DOCTYPE" in safe_text.upper() or "<!ENTITY" in safe_text.upper():
        raise ValueError("DTD/ENTITY запрещены в XML ТЭК-Торг")
    try:
        root = ET.fromstring(safe_text)
    except ET.ParseError as exc:
        raise ValueError(f"некорректный XML ТЭК-Торг: {exc}") from exc

    fault = next(_descendants(root, "Fault"), None)
    if fault is not None:
        detail = _child_text(fault, "faultstring") or "SOAP Fault"
        raise ValueError(detail)

    total_pages = _first_int(root, "totalPage", default=1)
    response_section = _first_text(root, "sectionCode") or section_code
    records = [
        record
        for procedure in _descendants(root, "procedure")
        if (record := _parse_procedure(procedure, source_url, response_section)) is not None
    ]
    return records, max(1, total_pages)


class TektorgSource:
    source_name = TEKTORG_SOURCE_NAME

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        section_codes: Iterable[str] = DEFAULT_SECTION_CODES,
        lookback_hours: int = 48,
        page_size: int = 100,
        max_pages_per_section: int = 10,
        timeout_seconds: int = 60,
        target_only: bool = True,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/xml,application/xml",
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "urn:procedures",
            }
        )
        self.section_codes = tuple(code.strip() for code in section_codes if code.strip())
        self.lookback_hours = max(1, lookback_hours)
        self.page_size = max(1, min(page_size, 1000))
        self.max_pages_per_section = max(1, max_pages_per_section)
        self.timeout_seconds = max(1, timeout_seconds)
        self.target_only = target_only
        self.now = now

    @classmethod
    def from_env(cls) -> "TektorgSource":
        section_codes = tuple(
            value.strip()
            for value in os.getenv("TEKTORG_SECTION_CODES", "").split(",")
            if value.strip()
        ) or DEFAULT_SECTION_CODES
        return cls(
            section_codes=section_codes,
            lookback_hours=_env_int("TEKTORG_LOOKBACK_HOURS", 48),
            page_size=_env_int("TEKTORG_PAGE_SIZE", 100),
            max_pages_per_section=_env_int("TEKTORG_MAX_PAGES_PER_SECTION", 10),
            timeout_seconds=_env_int("TEKTORG_TIMEOUT_SECONDS", 60),
            target_only=_env_bool("TEKTORG_TARGET_ONLY", True),
        )

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        del keywords  # The official feed is collected broadly, then filtered locally.
        end_update_at = self.now()
        start_update_at = end_update_at - timedelta(hours=self.lookback_hours)
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        successful_pages = 0

        for section_code in self.section_codes:
            for page in range(1, self.max_pages_per_section + 1):
                request_body = build_soap_request(
                    section_code=section_code,
                    page=page,
                    page_size=self.page_size,
                    start_update_at=start_update_at,
                    end_update_at=end_update_at,
                )
                try:
                    response = self.session.post(
                        TEKTORG_SOAP_URL,
                        data=request_body,
                        timeout=self.timeout_seconds,
                    )
                    response.raise_for_status()
                    records, total_pages = parse_soap_response(
                        response.content,
                        source_url=response.url,
                        section_code=section_code,
                    )
                    successful_pages += 1
                except (requests.RequestException, ValueError) as exc:
                    errors.append(f"{section_code}, стр. {page}: {exc}")
                    break

                for tender in records:
                    if self.target_only and not detect_region(
                        " ".join(
                            (
                                tender.region or "",
                                tender.delivery_region_evidence,
                            )
                        )
                    ):
                        continue
                    if tender.unique_key in seen:
                        continue
                    seen.add(tender.unique_key)
                    collected.append(tender)
                if page >= total_pages:
                    break

        if not collected and errors and not successful_pages:
            raise SourceFetchError(f"ТЭК-Торг API недоступен: {'; '.join(errors)}")
        return collected


def _parse_procedure(
    procedure: ET.Element,
    source_url: str,
    section_code: str,
) -> TenderRecord | None:
    title = _child_text(procedure, "title")
    registry_number = _child_text(procedure, "registryNumber")
    eis_number = _child_text(procedure, "eisRegistryNumber")
    remote_id = _child_text(procedure, "remoteId")
    number = registry_number or eis_number or remote_id
    url = _child_text(procedure, "url_to_showcase") or source_url
    lots = list(_descendants(_direct_child(procedure, "lots"), "lot"))
    lot_subjects = [_child_text(lot, "subject") for lot in lots]
    if not title:
        title = next((value for value in lot_subjects if value), "")
    if not title or not number:
        return None

    organizer = _direct_child(procedure, "organizer")
    customers = [
        customer
        for lot in lots
        for customer in _descendants(_direct_child(lot, "customers"), "customer")
    ]
    customer_node = next(
        (customer for customer in customers if _child_text(customer, "fullName")),
        None,
    )
    contact_node = customer_node or organizer
    customer = _child_text(contact_node, "fullName") or _child_text(organizer, "fullName") or None

    delivery_addresses = [
        _child_text(place, "address")
        for lot in lots
        for place in _descendants(_direct_child(lot, "deliveryPlaces"), "deliveryPlace")
        if _child_text(place, "address")
    ]
    customer_legal = _format_address(_direct_child(contact_node, "legal"))
    customer_postal = _format_address(_direct_child(contact_node, "postal"))
    organizer_legal = _format_address(_direct_child(organizer, "legal"))
    review_city = _child_text(procedure, "reviewApplicsCity")
    region_texts = [*delivery_addresses, customer_legal, customer_postal, organizer_legal, review_city]
    region = next((detect_region(value) for value in region_texts if detect_region(value)), None)

    currency = _child_text(procedure, "currency").upper()
    prices = [_parse_float(_child_text(lot, "startPrice")) for lot in lots]
    price = (
        sum(value for value in prices if value is not None)
        if currency in {"RUB", "RUR", "РУБ", "РУБЛЬ"} and any(value is not None for value in prices)
        else None
    )
    procedure_type = _child_text(_direct_child(procedure, "procedureType"), "title")
    section_label = SECTION_LABELS.get(section_code, section_code or "ТЭК-Торг")
    contact_email = (
        _child_text(contact_node, "email") or _child_text(procedure, "contactEmail")
    )
    contact_phone = (
        _child_text(contact_node, "phone") or _child_text(procedure, "contactPhone")
    )
    contact_person = _child_text(procedure, "contactPerson")
    inn = _child_text(contact_node, "inn") or _child_text(organizer, "inn")

    detail_parts = [title, *lot_subjects, customer or "", *delivery_addresses]
    for lot in lots:
        for tag in ("lotOkved", "lotOkved2", "nomenclature", "nomenclature2", "lotUnits"):
            node = _direct_child(lot, tag)
            if node is not None:
                detail_parts.extend(text for text in node.itertext() if text and text.strip())
    for document in _descendants(procedure, "document"):
        detail_parts.extend(
            value
            for value in (_child_text(document, "filename"), _child_text(document, "file"))
            if value
        )
    markers = {
        "TEKTORG_INN": inn,
        "TEKTORG_LEGAL_ADDRESS": customer_legal or organizer_legal,
        "TEKTORG_POSTAL_ADDRESS": customer_postal,
        "TEKTORG_EMAIL": contact_email,
        "TEKTORG_PHONE": contact_phone,
        "TEKTORG_CONTACT_PERSON": contact_person,
        "TEKTORG_CONTACT_SOURCE": url,
    }
    raw_text = " ".join(" ".join(detail_parts).split())[:16000]
    marker_text = "\n".join(f"{key}={value}" for key, value in markers.items() if value)
    if marker_text:
        raw_text = f"{raw_text}\n{marker_text}"

    official_url = _eis_search_url(eis_number) if eis_number else None
    procurement_law = _procurement_law(section_code, eis_number, procedure_type)
    return TenderRecord(
        title=title,
        url=url,
        source=TEKTORG_SOURCE_NAME,
        tender_number=number,
        customer=customer,
        region=region,
        price=price,
        deadline=_first_datetime(
            procedure,
            "dateEndRegistration",
            "dateEndRegistrationTech",
            "dateEndRegistrationCom",
            "dateEndSecondPartsReview",
        ),
        status=" · ".join(value for value in (procedure_type, section_label) if value),
        published_at=_parse_datetime(_child_text(procedure, "datePublished")),
        discovered_at=datetime.now(),
        raw_text=raw_text,
        delivery_region_evidence="; ".join(delivery_addresses),
        source_confidence=0.99,
        official_number=eis_number or None,
        official_url=official_url,
        official_source="eis-zakupki" if eis_number else None,
        platform_number=registry_number or remote_id or None,
        platform_url=url,
        procurement_law=procurement_law,
        resolution_method="tektorg-public-soap",
        resolution_confidence=1.0,
    )


def _typed_node(parent: ET.Element, name: str, xml_type: str, value: str) -> None:
    node = ET.SubElement(parent, name, {f"{{{XSI}}}type": xml_type})
    node.text = value


def _soap_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _direct_child(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((child for child in element if _local_name(child.tag) == name), None)


def _child_text(element: ET.Element | None, name: str) -> str:
    child = _direct_child(element, name)
    return " ".join("".join(child.itertext()).split()) if child is not None else ""


def _descendants(element: ET.Element | None, name: str):
    if element is None:
        return iter(())
    return (node for node in element.iter() if _local_name(node.tag) == name)


def _first_text(root: ET.Element, name: str) -> str:
    node = next(_descendants(root, name), None)
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _first_int(root: ET.Element, name: str, *, default: int) -> int:
    try:
        return int(_first_text(root, name))
    except (TypeError, ValueError):
        return default


def _first_datetime(element: ET.Element, *names: str) -> datetime | None:
    return next(
        (
            parsed
            for name in names
            if (parsed := _parse_datetime(_child_text(element, name))) is not None
        ),
        None,
    )


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _format_address(element: ET.Element | None) -> str:
    if element is None:
        return ""
    parts = [
        _child_text(element, name)
        for name in ("index", "region", "settlement", "city", "street", "house")
    ]
    return ", ".join(dict.fromkeys(value for value in parts if value))


def _eis_search_url(number: str) -> str:
    return (
        "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?"
        + urlencode({"searchString": number, "morphology": "on"})
    )


def _procurement_law(section_code: str, eis_number: str, procedure_type: str) -> str | None:
    digits = "".join(character for character in eis_number if character.isdigit())
    if section_code == "44fz" or len(digits) == 19:
        return "44-ФЗ"
    if len(digits) == 11 and digits.startswith("3"):
        return "223-ФЗ"
    if "223" in procedure_type or section_code == "zakupki":
        return "223-ФЗ / коммерческая закупка"
    return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off"}
