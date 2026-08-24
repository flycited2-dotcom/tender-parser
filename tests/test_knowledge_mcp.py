import asyncio
import os
import sqlite3
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tender_parser.knowledge_mcp import _search_tender_database


def test_knowledge_mcp_stdio_handshake_and_tools(tmp_path: Path) -> None:
    knowledge_root = tmp_path / "knowledge_base"
    knowledge_root.mkdir()
    (knowledge_root / "sample.txt").write_text("Тендерный документ", encoding="utf-8")

    async def verify() -> None:
        env = dict(os.environ)
        env["TENDER_KNOWLEDGE_BASE"] = str(knowledge_root)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tender_parser.knowledge_mcp"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == "tlt-tender-knowledge"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "knowledge_status",
                    "list_documents",
                    "search_documents",
                    "read_document",
                    "search_itp_products",
                    "search_climate_products",
                    "search_private_supplier_prices",
                    "route_product_search",
                    "search_tenders",
                }
                result = await session.call_tool("knowledge_status", {})
                assert result.is_error is False

    asyncio.run(verify())


def test_search_tender_database_returns_ranked_read_only_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "tenders.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE tenders (
                title TEXT, url TEXT, source TEXT, tender_number TEXT, customer TEXT,
                region TEXT, price REAL, deadline TEXT, category TEXT,
                review_priority TEXT, raw_text TEXT, last_seen_at TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO tenders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("Поставка роутеров", "https://example.test/1", "eis", "1", "Заказчик", "Крым",
             90000, "2026-09-01", "сети", "hot", "маршрутизаторы", "2026-08-21"),
        )
    result = _search_tender_database(db_path, "роутер", 5)
    assert result["total"] == 1
    assert result["tenders"][0]["tender_number"] == "1"
    assert "raw_text" not in result["tenders"][0]
