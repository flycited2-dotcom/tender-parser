from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Iterable

import requests

from tender_parser import config
from tender_parser.models import TenderRecord
from tender_parser.sources.roseltorg import target_regions
from tender_parser.sources.rts import SourceFetchError


SBERBANK_AST_SEARCH_URL = "https://utp.sberbank-ast.ru/Main/SearchQuery/UnitedPurchaseListNew"
SBERBANK_AST_REFERER = "https://utp.sberbank-ast.ru/Main/List/UnitedPurchaseListNew"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.4"
ACTIVE_STATES = "Подача заявок|;|Прием заявок"
SEARCH_FIELDS = [
    "TradeSectionId",
    "purchAmount",
    "purchCurrency",
    "purchCodeTerm",
    "purchCode",
    "PurchaseTypeName",
    "purchStateName",
    "UnitedStateName",
    "BidStatusName",
    "OrgName",
    "SourceTerm",
    "PublicDate",
    "RequestDate",
    "RequestStartDate",
    "RequestAcceptDate",
    "EndDate",
    "purchName",
    "BidName",
    "SourceHrefTerm",
    "objectHrefTerm",
    "IntegratorCode",
    "IntegratorCodeTerm",
    "RegionName",
    "RegionNameTerm",
]


def build_search_xml(query: str, region: str, *, page_size: int = 100) -> str:
    root = ET.Element("elasticrequest")
    filters = ET.SubElement(root, "filters")
    _value_node(filters, "mainSearchBar", query, extras={"type": "best_fields", "minimum_should_match": "100%"})
    _range_node(filters, "purchAmount")
    _range_node(filters, "PublicDate")
    _value_node(filters, "PurchaseStageTerm", "")
    _value_node(filters, "SourceTerm", "")
    _value_node(filters, "RegionNameTerm", region, visible=region)
    _range_node(filters, "RequestStartDate")
    _range_node(filters, "RequestDate")
    _range_node(filters, "AuctionBeginDate")
    _value_node(filters, "okdp2MultiMatch", "")
    okpd_tree = ET.SubElement(filters, "okdp2tree")
    for name in ("value", "productField", "branchField"):
        ET.SubElement(okpd_tree, name)
    for name in ("classifier", "organizator", "customer"):
        node = ET.SubElement(filters, name)
        ET.SubElement(node, "visiblepart")
    for name in ("orgCondition", "orgDictionary", "CustomerCondition", "CustomerDictionary"):
        _value_node(filters, name, "", visible=None)
    _value_node(filters, "purchStateNameTerm", ACTIVE_STATES, visible=ACTIVE_STATES)
    for name in (
        "BidStatusName",
        "PurchaseWayTerm",
        "PurchaseTypeNameTerm",
        "BranchNameTerm",
        "IsSMPTerm",
    ):
        _value_node(filters, name, "")

    fields = ET.SubElement(root, "fields")
    for field in SEARCH_FIELDS:
        ET.SubElement(fields, "field").text = field
    sort = ET.SubElement(root, "sort")
    ET.SubElement(sort, "value").text = "default"
    ET.SubElement(sort, "direction")
    aggregations = ET.SubElement(root, "aggregations")
    empty = ET.SubElement(aggregations, "empty")
    for name, value in (
        ("filterType", "filter_aggregation"),
        ("field", ""),
        ("min_doc_count", "0"),
        ("order", "asc"),
    ):
        ET.SubElement(empty, name).text = value
    ET.SubElement(root, "size").text = str(page_size)
    ET.SubElement(root, "from").text = "0"
    return ET.tostring(root, encoding="unicode")


def parse_search_payload(payload: object) -> list[TenderRecord]:
    if not isinstance(payload, dict) or payload.get("result") != "success":
        return []
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("Data"), dict):
        return []
    xml_text = data["Data"].get("tableXml")
    if not isinstance(xml_text, str):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    tenders: list[TenderRecord] = []
    for hit in root.findall("hits"):
        source = hit.find("_source")
        if source is None:
            continue
        title = _node_text(source, "purchName") or _node_text(source, "BidName")
        url = _node_text(source, "objectHrefTerm")
        number = (
            _node_text(source, "IntegratorCodeTerm")
            or _node_text(source, "purchCodeTerm")
            or _node_text(source, "purchCode")
        )
        if not title or not url or not number:
            continue
        amount = _parse_amount(_node_text(source, "purchAmount"))
        status = (
            _node_text(source, "UnitedStateName")
            or _node_text(source, "purchStateName")
            or _node_text(source, "BidStatusName")
        )
        section = _node_text(source, "SourceTerm")
        region = _node_text(source, "RegionNameTerm") or _node_text(source, "RegionName") or None
        raw_text = " ".join(
            value
            for value in [
                title,
                _node_text(source, "BidName"),
                _node_text(source, "OrgName"),
                region or "",
                section,
                _node_text(source, "PurchaseTypeName"),
            ]
            if value
        )
        tenders.append(
            TenderRecord(
                title=title,
                url=url,
                source="sberbank-ast",
                tender_number=number,
                customer=_node_text(source, "OrgName") or None,
                region=region,
                price=amount,
                deadline=_parse_datetime(_node_text(source, "RequestDate")),
                status=" · ".join(value for value in [status, section] if value),
                published_at=_parse_datetime(_node_text(source, "PublicDate")),
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class SberbankAstSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        regions: list[str] | None = None,
        timeout_seconds: int = 15,
        max_errors: int = 3,
        page_size: int = 100,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": SBERBANK_AST_REFERER,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.queries = queries or ["", *config.SEARCH_QUERY_TERMS]
        self.regions = target_regions(regions or config.SEARCH_REGION_TERMS)
        self.timeout_seconds = timeout_seconds
        self.max_errors = max_errors
        self.page_size = page_size

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        for query in self.queries or list(keywords):
            for _, region_name in self.regions:
                xml_data = build_search_xml(query, region_name, page_size=self.page_size)
                try:
                    response = self.session.post(
                        SBERBANK_AST_SEARCH_URL,
                        data={
                            "xmlData": xml_data,
                            "orgId": "0",
                            "buId": "0",
                            "personId": "0",
                            "buMainId": "0",
                            "personMainId": "0",
                        },
                        timeout=self.timeout_seconds,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (requests.RequestException, ValueError, TypeError) as exc:
                    errors.append(f"{query}/{region_name}: {exc}")
                    if len(errors) >= self.max_errors:
                        break
                    continue
                for tender in parse_search_payload(payload):
                    if tender.unique_key in seen:
                        continue
                    seen.add(tender.unique_key)
                    collected.append(tender)
            if len(errors) >= self.max_errors:
                break

        if not collected and errors:
            raise SourceFetchError(f"Сбербанк-АСТ недоступен: {'; '.join(errors)}")
        return collected


def _value_node(
    parent: ET.Element,
    name: str,
    value: str,
    *,
    visible: str | None = "",
    extras: dict[str, str] | None = None,
) -> None:
    node = ET.SubElement(parent, name)
    ET.SubElement(node, "value").text = value
    if extras:
        for extra_name, extra_value in extras.items():
            ET.SubElement(node, extra_name).text = extra_value
    if visible is not None:
        ET.SubElement(node, "visiblepart").text = visible


def _range_node(parent: ET.Element, name: str) -> None:
    node = ET.SubElement(parent, name)
    ET.SubElement(node, "minvalue")
    ET.SubElement(node, "maxvalue")


def _node_text(parent: ET.Element, name: str) -> str:
    value = parent.findtext(name)
    return " ".join(value.split()) if value else ""


def _parse_amount(value: str) -> float | None:
    try:
        amount = float(value.replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return amount if amount > 0 else None


def _parse_datetime(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.year >= 2079:
            return None
        return parsed
    return None
