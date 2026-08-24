import csv
from pathlib import Path

from tender_parser.supplier_search import SupplierProduct, search_case_products


class RecordingGateway:
    def __init__(self, source: str, products: list[SupplierProduct]) -> None:
        self.source = source
        self.products = products
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 10):
        self.queries.append(query)
        return len(self.products), self.products[:limit]


def _case(tmp_path: Path, name: str) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    with (case_dir / "items.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["line_id", "name", "quantity", "unit", "required_specs", "mandatory"])
        writer.writerow(["1", name, "1", "шт", "", "yes"])
    (case_dir / "case.json").write_text('{"title":"test"}', encoding="utf-8")
    (case_dir / "offers.csv").write_text("", encoding="utf-8")
    (case_dir / "expenses.csv").write_text("", encoding="utf-8")
    return case_dir


def test_climate_line_uses_climate_gateway_first(tmp_path: Path) -> None:
    itp = RecordingGateway("itp", [])
    climate = RecordingGateway(
        "climate",
        [SupplierProduct("AC-12", "Сплит-система 12", 30000, "available", True, 0, source="breeze")],
    )
    results = search_case_products(_case(tmp_path, "Поставка кондиционера"), itp, climate_gateway=climate)
    assert results[0].products[0].source == "breeze"
    assert climate.queries
    assert not itp.queries


def test_climate_line_falls_back_to_itp_when_hub_has_no_result(tmp_path: Path) -> None:
    itp = RecordingGateway(
        "itp", [SupplierProduct("ITP-1", "Кондиционер", 35000, "available", True, 2, source="itp")]
    )
    climate = RecordingGateway("climate", [])
    results = search_case_products(_case(tmp_path, "Монтаж сплит-системы"), itp, climate_gateway=climate)
    assert results[0].products[0].source == "itp"
    assert climate.queries
    assert itp.queries


def test_non_climate_line_skips_climate_gateway(tmp_path: Path) -> None:
    itp = RecordingGateway("itp", [])
    climate = RecordingGateway("climate", [])
    search_case_products(_case(tmp_path, "Ноутбук"), itp, climate_gateway=climate)
    assert itp.queries
    assert not climate.queries
