from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_silent_launcher_defaults_to_fast_profile() -> None:
    text = (ROOT / "run_tender_parser_silent.bat").read_text(encoding="utf-8")

    assert "set \"TENDER_PARSER_PROFILE=fast\"" in text
    assert "python -m tender_parser run --profile %TENDER_PARSER_PROFILE%" in text


def test_daily_scheduler_accepts_profile_argument() -> None:
    text = (ROOT / "Настроить_ежедневный_запуск.ps1").read_text(encoding="utf-8")

    assert "[ValidateSet('full', 'fast', 'local', 'rts')]" in text
    assert "[string]$Profile = 'fast'" in text
    assert "TENDER_PARSER_PROFILE=$Profile" in text


def test_eat_setup_helper_writes_ignored_env_file() -> None:
    text = (ROOT / "Настроить_EAT_env.ps1").read_text(encoding="utf-8")

    assert "EAT_API_TOKEN=$ApiToken" in text
    assert "EAT_EXT_SYSTEM=$ExtSystem" in text
    assert "check-env" in text


def test_rts_accumulator_launchers_call_expected_commands() -> None:
    add_text = (ROOT / "Добавить_страницу_RTS.bat").read_text(encoding="utf-8")
    report_text = (ROOT / "Отчет_по_накопленному_RTS.bat").read_text(encoding="utf-8")

    assert "python -m tender_parser rts-add-page" in add_text
    assert "python -m tender_parser run --profile rts-accumulated" in report_text
    assert "exports\\latest.html" in report_text
    watch_text = (ROOT / "Автосбор_RTS_кабинета.bat").read_text(encoding="utf-8")
    assert "python -m tender_parser rts-watch" in watch_text


def test_rts_cabinet_launchers_use_isolated_chrome_profile() -> None:
    open_text = (ROOT / "Открыть_RTS_кабинет_Chrome.bat").read_text(encoding="utf-8")
    collect_text = (ROOT / "Собрать_RTS_кабинет.bat").read_text(encoding="utf-8")

    assert "--remote-debugging-address=127.0.0.1" in open_text
    assert "--remote-debugging-port=9222" in open_text
    assert "browser_profiles\\rts_chrome" in open_text
    assert "python -m tender_parser run --profile rts-cabinet" in collect_text
