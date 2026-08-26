import json
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


class FakePage:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self._html = html
        self.badges: list[str] = []

    def content(self) -> str:
        return self._html

    def evaluate(self, script: str, arg: str) -> None:
        self.badges.append(arg)


def test_poll_page_reports_sqlite_errors(tmp_path: Path, capsys) -> None:
    import sqlite3

    from tender_parser.browser.rts_watcher import RtsCabinetWatcher

    class LockedAccumulator:
        def add_many(self, tenders):  # noqa: ANN001
            raise sqlite3.OperationalError("database is locked")

    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    watcher = RtsCabinetWatcher(tmp_path / "tenders.db")
    page = FakePage(RESULTS_URL, html)

    watcher._poll_page(page, LockedAccumulator())

    output = capsys.readouterr().out
    assert "база данных" in output or "database is locked" in output


def test_poll_page_snapshots_poisk_once_per_url(tmp_path: Path) -> None:
    from tender_parser.browser.rts_watcher import RtsCabinetWatcher

    watcher = RtsCabinetWatcher(tmp_path / "tenders.db", diagnostics_dir=tmp_path / "diag")
    accumulator = RtsAccumulator(tmp_path / "tenders.db")
    url = "https://www.rts-tender.ru/poisk/search?id=abc"

    watcher._poll_page(FakePage(url, "<html>вариант 1 счетчик 10:01</html>"), accumulator)
    watcher._poll_page(FakePage(url, "<html>вариант 2 счетчик 10:02</html>"), accumulator)
    watcher._poll_page(FakePage(url, "<html>вариант 3 счетчик 10:03</html>"), accumulator)

    snapshots = list((tmp_path / "diag").glob("*.html"))
    assert len(snapshots) == 1


def test_poll_page_records_healthy_session(tmp_path: Path) -> None:
    from tender_parser.browser.rts_watcher import RtsCabinetWatcher

    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    watcher = RtsCabinetWatcher(tmp_path / "tenders.db")
    accumulator = RtsAccumulator(tmp_path / "tenders.db")

    watcher._poll_page(FakePage(RESULTS_URL, html), accumulator)

    health = json.loads((tmp_path / "rts_watcher_health.json").read_text(encoding="utf-8"))
    assert health["status"] == "results"
    assert health["accumulated_total"] > 0


def test_poll_page_records_login_without_reloading(tmp_path: Path) -> None:
    from tender_parser.browser.rts_watcher import RtsCabinetWatcher

    html = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    watcher = RtsCabinetWatcher(tmp_path / "tenders.db")
    page = FakePage("https://223.rts-tender.ru/login", html)

    watcher._poll_page(page, RtsAccumulator(tmp_path / "tenders.db"))

    health = json.loads((tmp_path / "rts_watcher_health.json").read_text(encoding="utf-8"))
    assert health["status"] == "login"
    assert page.badges == ["Требуется ручной вход в RTS"]


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
