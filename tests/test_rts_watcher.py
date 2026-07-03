from pathlib import Path

from tender_parser.browser.rts_watcher import badge_text, collect_from_page, save_snapshot
from tender_parser.rts_accumulator import RtsAccumulator

FIXTURES = Path("tests/fixtures")
RESULTS_URL = "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx"


def test_collect_from_page_adds_visible_rows_to_accumulator(tmp_path: Path) -> None:
    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    accumulator = RtsAccumulator(tmp_path / "tenders.db")

    result = collect_from_page(html, RESULTS_URL, accumulator)

    assert result is not None
    added, total = result
    assert added > 0
    assert total == added
    assert len(accumulator.load_all()) == total


def test_collect_from_page_is_idempotent(tmp_path: Path) -> None:
    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    accumulator = RtsAccumulator(tmp_path / "tenders.db")

    first = collect_from_page(html, RESULTS_URL, accumulator)
    second = collect_from_page(html, RESULTS_URL, accumulator)

    assert first is not None and second is not None
    assert second[0] == 0
    assert second[1] == first[1]


def test_collect_from_page_skips_login_page(tmp_path: Path) -> None:
    html = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    accumulator = RtsAccumulator(tmp_path / "tenders.db")

    result = collect_from_page(html, "https://www.rts-tender.ru/login", accumulator)

    assert result is None
    assert accumulator.load_all() == []


def test_badge_text_reports_totals() -> None:
    assert badge_text(12, 112) == "Накопитель RTS: 112 строк (+12 новых)"
    assert badge_text(0, 112) == "Накопитель RTS: 112 строк (страница уже добавлена)"


def test_save_snapshot_writes_once_per_content(tmp_path: Path) -> None:
    seen: set[str] = set()
    url = "https://www.rts-tender.ru/poisk/search?id=abc"

    first = save_snapshot("<html>результаты</html>", url, tmp_path / "diagnostics", seen)
    second = save_snapshot("<html>результаты</html>", url, tmp_path / "diagnostics", seen)
    other = save_snapshot("<html>другая страница</html>", url, tmp_path / "diagnostics", seen)

    assert first is not None and first.exists()
    assert url in first.read_text(encoding="utf-8")
    assert second is None
    assert other is not None and other.name != first.name
