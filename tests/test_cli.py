from datetime import datetime
from pathlib import Path

from tender_parser.cli import _all_keywords, build_default_source, run
from tender_parser.models import TenderRecord
from tender_parser.sources.composite import CompositeSource
from tender_parser.sources.eat import EatIntegrationSource
from tender_parser.sources.eis import EisZakupkiSource
from tender_parser.sources.etp_gpb import EtpGpbRssSource
from tender_parser.sources.rostender import RostenderSource
from tender_parser.sources.rts import SourceFetchError
from tender_parser.sources.tender_pro import TenderProSource
from tender_parser.sources.torgi82 import Torgi82Source


class FakeSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return [
            TenderRecord(
                title="Поставка МФУ в Республику Крым",
                url="https://example.test/tender-1/",
                source="fake",
                tender_number="1",
                customer="Заказчик",
                region="Республика Крым",
                price=45_000.0,
                deadline=datetime(2026, 5, 25, 10, 0),
                raw_text="Поставка МФУ в Республику Крым",
            )
        ]


class EmptySource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return []


class BlockedSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        raise SourceFetchError("все источники RTS недоступны")


class ReviewSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return [
            TenderRecord(
                title="Поставка МФУ в Республику Крым",
                url="https://example.test/tender-1/",
                source="fake",
                tender_number="1",
                customer="Заказчик",
                region="Республика Крым",
                price=45_000.0,
                deadline=datetime(2026, 5, 25, 10, 0),
                raw_text="Поставка МФУ в Республику Крым",
            ),
            TenderRecord(
                title="Оказание услуг по техническому обслуживанию кондиционеров",
                url="https://example.test/tender-2/",
                source="fake",
                tender_number="2",
                customer="Заказчик",
                price=120_000.0,
                deadline=datetime(2026, 5, 25, 10, 0),
                raw_text="Оказание услуг по техническому обслуживанию кондиционеров",
            ),
        ]


def test_run_dry_mode_creates_export_dirs(tmp_path: Path) -> None:
    result = run(["--dry-run", "--base-dir", str(tmp_path)])

    assert result == 0
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "exports").is_dir()


def test_all_keywords_includes_broad_aliases_and_regions() -> None:
    keywords = _all_keywords()

    assert "оргтехника" in keywords
    assert "офисная техника" in keywords
    assert "Симферополь" in keywords
    assert "Республика Крым" in keywords


def test_build_default_source_uses_composite_source() -> None:
    source = build_default_source()

    assert isinstance(source, CompositeSource)
    first_layer = source.sources[0]
    assert isinstance(first_layer, CompositeSource)
    assert isinstance(first_layer.sources[0], EtpGpbRssSource)
    assert isinstance(first_layer.sources[1], TenderProSource)
    assert isinstance(first_layer.sources[2], Torgi82Source)
    assert isinstance(first_layer.sources[3], EatIntegrationSource)
    assert isinstance(first_layer.sources[4], EisZakupkiSource)
    assert isinstance(first_layer.sources[5], RostenderSource)


def test_run_with_fake_source_creates_database_and_exports(tmp_path: Path) -> None:
    result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=FakeSource(),
    )

    assert result == 0
    assert (tmp_path / "data" / "tenders.db").exists()
    assert (tmp_path / "exports" / "latest.json").exists()
    assert list((tmp_path / "exports").glob("tenders_*.xlsx"))


def test_run_exports_only_current_run_matches(tmp_path: Path) -> None:
    first_result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=FakeSource(),
    )
    second_result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-20T12:00:00"],
        source=EmptySource(),
    )

    assert first_result == 0
    assert second_result == 0
    assert '"count": 0' in (tmp_path / "exports" / "latest.json").read_text(encoding="utf-8")


def test_run_keeps_existing_exports_when_source_is_blocked(tmp_path: Path) -> None:
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    latest = exports_dir / "latest.json"
    latest.write_text("old report", encoding="utf-8")

    result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=BlockedSource(),
    )

    assert result == 2
    assert latest.read_text(encoding="utf-8") == "old report"


def test_run_exports_review_items_for_manual_check(tmp_path: Path) -> None:
    result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=ReviewSource(),
    )

    latest = (tmp_path / "exports" / "latest.json").read_text(encoding="utf-8")

    assert result == 0
    assert '"count": 2' in latest
    assert '"filter_status": "matched"' in latest
    assert '"filter_status": "review"' in latest
