from __future__ import annotations

import sqlite3
from pathlib import Path

from tender_parser.supplier_search import SupplierProduct
from tender_parser.telegram_agent import (
    AgentRuntimeState,
    CatalogSearchTool,
    TelegramAgentSettings,
    TenderDatabaseSearchTool,
    _latest_tender_report,
    _launch_collector_browser,
    _thread_id_from_jsonl,
    split_message,
    telegram_html,
)


def test_latest_tender_report_selects_newest_excel(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    older = exports / "tenders_2026-08-25.xlsx"
    newer = exports / "tenders_2026-08-26.xlsx"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    older.touch()
    newer.touch()

    assert _latest_tender_report(tmp_path) == newer


def test_collector_browser_requires_launcher(tmp_path: Path) -> None:
    assert _launch_collector_browser(tmp_path) is False


def test_agent_uses_dedicated_telegram_credentials(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "daily-report-token")
    monkeypatch.setenv("TELEGRAM_AGENT_BOT_TOKEN", "personal-agent-token")
    monkeypatch.setenv("TELEGRAM_AGENT_ALLOWED_USER_IDS", "123, 456")
    monkeypatch.setenv("CODEX_CLI_PATH", "codex.exe")

    settings = TelegramAgentSettings.from_environment(tmp_path)

    assert settings.telegram_token == "personal-agent-token"
    assert settings.allowed_user_ids == frozenset({123, 456})


class FakeGateway:
    def __init__(self, products: list[SupplierProduct]) -> None:
        self.products = products
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 10):
        self.queries.append(query)
        return len(self.products), self.products[:limit]


def test_catalog_search_returns_compact_read_only_results() -> None:
    gateway = FakeGateway(
        [
            SupplierProduct(
                sku="SKU-1",
                name="Принтер Test 1000",
                purchase_price_gross=12345.0,
                stock_status="available",
                is_available=True,
                delivery_days=2,
                vendor="Test",
                product_url="https://example.test/product",
            )
        ]
    )
    result = CatalogSearchTool(gateway).search("принтер", "", 5)
    assert result["route"] == "itp"
    assert result["products"][0]["sku"] == "SKU-1"
    assert result["products"][0]["price_gross"] == 12345.0
    assert gateway.queries == ["принтер"]


def test_climate_route_has_priority() -> None:
    climate = FakeGateway(
        [SupplierProduct("C-1", "Сплит-система", 100.0, "available", True, 1)]
    )
    standard = FakeGateway([])
    result = CatalogSearchTool(standard, climate).search("кондиционер", "", 5)
    assert result["route"] == "climate"
    assert climate.queries == ["кондиционер"]
    assert standard.queries == []


def test_tender_database_search_is_read_only(tmp_path: Path) -> None:
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
            ("Поставка принтеров", "https://example.test/1", "eis", "1", "Заказчик", "Крым", 50000,
             "2026-09-01", "оргтехника", "hot", "лазерный принтер", "2026-08-21"),
        )
    result = TenderDatabaseSearchTool(db_path).search("принтер", 5)
    assert result["total"] == 1
    assert result["tenders"][0]["tender_number"] == "1"
    assert "raw_text" not in result["tenders"][0]


def test_split_message_respects_telegram_limit() -> None:
    chunks = split_message(("строка данных\n" * 500).strip(), limit=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_thread_id_is_read_from_codex_jsonl() -> None:
    output = 'warning\n{"type":"thread.started","thread_id":"thread-123"}\n'
    assert _thread_id_from_jsonl(output) == "thread-123"


def test_agent_runtime_state_is_enabled_by_default_and_persists(tmp_path: Path) -> None:
    state_path = tmp_path / "telegram_agent_state.json"
    state = AgentRuntimeState(state_path)
    assert state.is_enabled() is True

    state.set_enabled(False)
    assert AgentRuntimeState(state_path).is_enabled() is False

    AgentRuntimeState(state_path).set_enabled(True)
    assert AgentRuntimeState(state_path).is_enabled() is True


def test_telegram_html_formats_supported_markdown_safely() -> None:
    value = telegram_html(
        "## Результат\n\n- **Принтер <A>** — [источник](https://example.test/a)"
    )
    assert "<b>Результат</b>" in value
    assert "• <b>Принтер &lt;A&gt;</b>" in value
    assert '<a href="https://example.test/a">источник</a>' in value
