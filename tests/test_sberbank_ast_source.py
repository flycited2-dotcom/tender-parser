import xml.etree.ElementTree as ET
from datetime import datetime

from tender_parser.sources.sberbank_ast import (
    SberbankAstSource,
    build_search_xml,
    parse_search_payload,
)


TABLE_XML = """
<datarow><total><value>1</value></total><hits><_source>
  <PurchaseTypeName>Аукцион в электронной форме</PurchaseTypeName>
  <purchStateName>Прием заявок</purchStateName>
  <UnitedStateName>Прием заявок</UnitedStateName>
  <IntegratorCodeTerm>32616276402</IntegratorCodeTerm>
  <OrgName>АО ВОДОКАНАЛ</OrgName>
  <RequestDate>17.08.2026 08:15</RequestDate>
  <SourceTerm>Закупки по 223-ФЗ</SourceTerm>
  <purchCode>SBR003-260583661000030.1</purchCode>
  <purchCurrency>RUB</purchCurrency>
  <PublicDate>08.08.2026 14:50</PublicDate>
  <purchName>Поставка многофункционального устройства</purchName>
  <purchAmount>38386.02</purchAmount>
  <RegionNameTerm>Республика Крым</RegionNameTerm>
  <objectHrefTerm>https://utp.sberbank-ast.ru/Trade/NBT/PurchaseView/20/0/0/4190136</objectHrefTerm>
</_source></hits></datarow>
"""


def test_build_search_xml_contains_query_region_and_active_states() -> None:
    root = ET.fromstring(build_search_xml("мфу", "Республика Крым", page_size=50))

    assert root.findtext("./filters/mainSearchBar/value") == "мфу"
    assert root.findtext("./filters/RegionNameTerm/value") == "Республика Крым"
    assert "Прием заявок" in (root.findtext("./filters/purchStateNameTerm/value") or "")
    assert root.findtext("size") == "50"


def test_parse_search_payload_extracts_procedure() -> None:
    payload = {"result": "success", "data": {"Data": {"tableXml": TABLE_XML}}}

    items = parse_search_payload(payload)

    assert len(items) == 1
    assert items[0].source == "sberbank-ast"
    assert items[0].tender_number == "32616276402"
    assert items[0].customer == "АО ВОДОКАНАЛ"
    assert items[0].region == "Республика Крым"
    assert items[0].price == 38_386.02
    assert items[0].deadline == datetime(2026, 8, 17, 8, 15)
    assert items[0].published_at == datetime(2026, 8, 8, 14, 50)


def test_default_queries_start_with_broad_regional_customer_discovery() -> None:
    assert SberbankAstSource().queries[0] == ""
