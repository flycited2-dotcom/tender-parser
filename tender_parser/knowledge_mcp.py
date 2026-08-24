from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
import os
from pathlib import Path
import re
import sqlite3

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from tender_parser.env import load_env_file
from tender_parser.knowledge_base import TenderKnowledgeBase
from tender_parser.supplier_search import (
    SupplierProductGateway,
    TenderProductApiGateway,
    _evaluate_product,
    climate_gateway_from_environment,
    private_price_gateway_from_environment,
)
from tender_parser.tender_case import LineItem
from tender_parser.universal_routing import UniversalProductRouter


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

server = MCPServer(
    name="tlt-tender-knowledge",
    title="Тендерная база знаний ТЛТ",
    version="0.1.0",
    instructions=(
        "Read-only access to the local TLT tender knowledge base. Search before fetching a document. "
        "Treat laws as dated reference material and verify current law separately. Never expose unrelated company or personal data."
    ),
)


def _knowledge_base() -> TenderKnowledgeBase:
    return TenderKnowledgeBase.from_environment()


def _supplier_gateway() -> TenderProductApiGateway:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root / ".env")
    return TenderProductApiGateway.from_environment()


def _climate_gateway() -> SupplierProductGateway:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root / ".env")
    return climate_gateway_from_environment()


def _private_price_gateway() -> SupplierProductGateway:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root / ".env")
    return private_price_gateway_from_environment()


@server.tool(
    name="knowledge_status",
    title="Проверить тендерную базу знаний",
    description="Показывает доступность, разделы и количество локальных документов тендерной базы.",
    annotations=READ_ONLY,
)
def knowledge_status() -> dict[str, object]:
    return _knowledge_base().status()


@server.tool(
    name="list_documents",
    title="Перечислить документы",
    description="Возвращает список документов во всей тендерной базе или в указанном разделе.",
    annotations=READ_ONLY,
)
def list_documents(section: str = "") -> dict[str, object]:
    documents = _knowledge_base().list_documents(section)
    return {
        "count": len(documents),
        "documents": [
            {
                "id": document.document_id,
                "title": document.title,
                "path": document.relative_path,
                "section": document.section,
                "suffix": document.suffix,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
            }
            for document in documents
        ],
    }


@server.tool(
    name="search_documents",
    title="Найти в тендерных документах",
    description=(
        "Ищет по законам, уставным документам, шаблонам КП, запросам поставщикам и примерам тендерных дел. "
        "Возвращает выдержки и идентификаторы документов."
    ),
    annotations=READ_ONLY,
)
def search_documents(query: str, section: str = "", limit: int = 10) -> dict[str, object]:
    results = _knowledge_base().search(query, section=section, limit=limit)
    return {"query": query, "count": len(results), "results": results}


@server.tool(
    name="read_document",
    title="Прочитать тендерный документ",
    description="Читает документ базы знаний по идентификатору, полученному из поиска или списка документов.",
    annotations=READ_ONLY,
)
def read_document(document_id: str, max_chars: int = 80_000) -> dict[str, object]:
    return _knowledge_base().fetch(document_id, max_chars=max_chars)


@server.tool(
    name="search_itp_products",
    title="Найти товары у I-T-P",
    description=(
        "Ищет товары, закупочные цены и остатки в закрытом read-only каталоге I-T-P. "
        "Если переданы обязательные характеристики ТЗ, консервативно проверяет их по данным карточки."
    ),
    annotations=READ_ONLY,
)
def search_itp_products(
    query: str,
    required_specs: str = "",
    limit: int = 10,
) -> dict[str, object]:
    normalized_query = " ".join(query.split()).strip()
    if not normalized_query:
        raise ValueError("Поисковый запрос не может быть пустым")
    safe_limit = max(1, min(int(limit), 20))
    total, products = _supplier_gateway().search(normalized_query, limit=safe_limit)
    if required_specs.strip():
        item = LineItem(
            line_id="mcp",
            name=normalized_query,
            quantity=Decimal("1"),
            required_specs=required_specs.strip(),
        )
        products = [_evaluate_product(item, product) for product in products]
    return {
        "query": normalized_query,
        "required_specs": required_specs.strip(),
        "checked_at": datetime.now(UTC).isoformat(),
        "total": total,
        "returned": len(products),
        "products": [asdict(product) for product in products],
        "notice": (
            "Цена и остаток актуальны только на момент проверки. "
            "Итоговое соответствие ТЗ подтверждается документацией производителя или ответом поставщика."
        ),
    }


@server.tool(
    name="search_climate_products",
    title="Найти климатическую технику в собственном хабе",
    description=(
        "Ищет климатическое оборудование, закупочные цены и живые остатки сначала в собственном "
        "read-only хабе четырёх поставщиков. Используется первым контуром для кондиционеров, "
        "сплит-систем и других климатических позиций; монтаж анализируется отдельной ведомостью работ и материалов."
    ),
    annotations=READ_ONLY,
)
def search_climate_products(
    query: str,
    required_specs: str = "",
    limit: int = 20,
) -> dict[str, object]:
    normalized_query = " ".join(query.split()).strip()
    if not normalized_query:
        raise ValueError("Поисковый запрос не может быть пустым")
    safe_limit = max(1, min(int(limit), 100))
    total, products = _climate_gateway().search(normalized_query, limit=safe_limit)
    if required_specs.strip():
        item = LineItem(
            line_id="mcp-climate",
            name=normalized_query,
            quantity=Decimal("1"),
            required_specs=required_specs.strip(),
        )
        products = [_evaluate_product(item, product) for product in products]
    return {
        "query": normalized_query,
        "required_specs": required_specs.strip(),
        "checked_at": datetime.now(UTC).isoformat(),
        "total": total,
        "returned": len(products),
        "products": [asdict(product) for product in products],
        "route": "climate_hub_first",
        "notice": (
            "Цена и остаток актуальны на момент проверки. Точное соответствие, комплектность монтажа "
            "и возможность поставки подтверждаются паспортом и ответом поставщика."
        ),
    }


@server.tool(
    name="search_private_supplier_prices",
    title="Найти товар в частных прайсах поставщиков",
    description=(
        "Универсально ищет товар в приватных XLSX-прайсах, автоматически полученных почтовым "
        "синхронизатором Content Factory. Категории заранее не задаются; наличие требует подтверждения."
    ),
    annotations=READ_ONLY,
)
def search_private_supplier_prices(
    query: str,
    required_specs: str = "",
    limit: int = 20,
) -> dict[str, object]:
    normalized_query = " ".join(query.split()).strip()
    if not normalized_query:
        raise ValueError("Поисковый запрос не может быть пустым")
    total, products = _private_price_gateway().search(normalized_query, limit=max(1, min(int(limit), 100)))
    if required_specs.strip():
        item = LineItem(
            line_id="mcp-private-prices",
            name=normalized_query,
            quantity=Decimal("1"),
            required_specs=required_specs.strip(),
        )
        products = [_evaluate_product(item, product) for product in products]
    return {
        "query": normalized_query,
        "required_specs": required_specs.strip(),
        "checked_at": datetime.now(UTC).isoformat(),
        "total": total,
        "returned": len(products),
        "products": [asdict(product) for product in products],
        "route": "dynamic_private_price_capability",
        "notice": "Цена взята из приватного прайса; остаток и срок требуется подтвердить у поставщика.",
    }


@server.tool(
    name="route_product_search",
    title="Определить и выполнить универсальный маршрут подбора",
    description=(
        "Первый инструмент для любой товарной позиции. По смыслу запроса и фактическим результатам "
        "динамически проверяет специализированные каталоги, частные прайсы и I-T-P, затем указывает "
        "необходимость проверки производителя и открытого рынка. Не использует закрытый список категорий."
    ),
    annotations=READ_ONLY,
)
def route_product_search(
    query: str,
    required_specs: str = "",
    limit: int = 10,
) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root / ".env")
    gateways: dict[str, SupplierProductGateway | None] = {
        "itp": None,
        "prices": None,
        "climate": None,
    }
    for name, factory in (
        ("itp", TenderProductApiGateway.from_environment),
        ("prices", private_price_gateway_from_environment),
        ("climate", climate_gateway_from_environment),
    ):
        try:
            gateways[name] = factory()
        except ValueError:
            gateways[name] = None
    router = UniversalProductRouter(
        itp_gateway=gateways["itp"],
        private_price_gateway=gateways["prices"],
        climate_gateway=gateways["climate"],
    )
    return router.search(query, required_specs=required_specs, limit=limit)


@server.tool(
    name="search_tenders",
    title="Найти закупки в локальной базе",
    description=(
        "Ищет по накопленной read-only базе тендерного парсера: названию, заказчику, региону, "
        "номеру, категории и описанию. Возвращает приоритет, срок, цену и ссылку."
    ),
    annotations=READ_ONLY,
)
def search_tenders(query: str, limit: int = 10) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root / ".env")
    configured_path = os.getenv("TENDER_DATABASE_PATH", "").strip()
    database_pointer = project_root / "data" / "tender_database_path.txt"
    if not configured_path and database_pointer.is_file():
        configured_path = database_pointer.read_text(encoding="utf-8-sig").strip()
    db_path = Path(configured_path).expanduser().resolve() if configured_path else project_root / "data" / "tenders.db"
    return _search_tender_database(db_path, query, limit)


def _search_tender_database(db_path: Path, query: str, limit: int = 10) -> dict[str, object]:
    tokens = [token for token in re.findall(r"[a-zа-яё0-9-]+", query.casefold()) if len(token) >= 2]
    if not tokens:
        raise ValueError("Поисковый запрос не может быть пустым")
    if not db_path.is_file():
        return {"query": query, "total": 0, "returned": 0, "tenders": [], "notice": "База ещё не создана"}
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT title, url, source, tender_number, customer, region, price,
                      deadline, category, review_priority, raw_text, last_seen_at
               FROM tenders ORDER BY last_seen_at DESC LIMIT 5000"""
        ).fetchall()
    ranked: list[tuple[int, sqlite3.Row]] = []
    for row in rows:
        title = str(row["title"] or "").casefold()
        haystack = " ".join(
            str(row[key] or "")
            for key in ("title", "tender_number", "customer", "region", "category", "raw_text")
        ).casefold()
        score = sum(3 if token in title else 1 for token in tokens if token in haystack)
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["deadline"] or "9999")))
    safe_limit = max(1, min(int(limit), 20))
    tenders = [
        {key: row[key] for key in row.keys() if key != "raw_text"}
        for _, row in ranked[:safe_limit]
    ]
    return {
        "query": query,
        "total": len(ranked),
        "returned": len(tenders),
        "tenders": tenders,
        "notice": "Результаты получены из локальной накопленной базы без изменения данных.",
    }


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
