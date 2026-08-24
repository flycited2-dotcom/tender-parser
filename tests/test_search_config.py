from datetime import datetime
from pathlib import Path
from shutil import copyfile

from tender_parser import config
from tender_parser.cli import run
from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord
from tender_parser.search_config import (
    DEFAULT_SEARCH_PROFILE,
    SearchProfile,
    apply_search_profile,
    load_search_profile,
)


WORKBOOK_PATH = Path("config/Настройки_поиска.xlsx")


def test_generated_search_workbook_round_trips_current_dictionary() -> None:
    profile = load_search_profile(WORKBOOK_PATH)

    assert len(profile.category_keywords) == 10
    assert "компьютер" in profile.category_keywords["Компьютерная техника и периферия"]
    assert "источник бесперебойного питания" in profile.category_keywords[
        "Резервное электропитание и ИБП"
    ]
    assert "шкаф архивный" in profile.category_keywords[
        "Офисная, архивная и складская мебель"
    ]
    assert "электротехническая продукция" in profile.search_terms
    assert "кабельная продукция" in profile.search_terms
    assert "медицин" in profile.stop_terms
    assert "продажа неликвидов" in profile.stop_terms
    assert profile.regions == [
        "Республика Крым",
        "Севастополь",
        "Запорожская область",
        "Херсонская область",
        "Симферополь",
    ]
    assert profile.min_price_rub == 30_000


def test_apply_search_profile_changes_matching_and_minimum_price() -> None:
    focused_rts_queries = list(config.RTS_SEARCH_QUERIES)
    custom = SearchProfile(
        category_keywords={"Спецтовары": ["термопринтер"]},
        search_terms=["термопринтер"],
        stop_terms=[],
        regions=["Республика Крым"],
        min_price_rub=100_000,
    )
    try:
        apply_search_profile(custom)
        result = evaluate_tender(
            TenderRecord(
                title="Поставка термопринтеров",
                url="https://example.test/thermal",
                source="test",
                region="Республика Крым",
                price=90_000,
                deadline=datetime(2026, 9, 1),
                raw_text="Поставка термопринтеров в Республику Крым",
            ),
            now=datetime(2026, 8, 1),
        )

        assert config.SEARCH_QUERY_TERMS == ["термопринтер"]
        assert config.RTS_SEARCH_QUERIES == focused_rts_queries
        assert result.filter_status == "excluded"
        assert "100000" in result.exclude_reason
    finally:
        apply_search_profile(DEFAULT_SEARCH_PROFILE)


def test_cli_loads_workbook_from_project_config_folder(tmp_path: Path, capsys) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    copyfile(WORKBOOK_PATH, config_dir / "Настройки_поиска.xlsx")

    result = run(["--dry-run", "--base-dir", str(tmp_path)])
    output = capsys.readouterr().out

    assert result == 0
    assert "Словарь Excel: загружен" in output
