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
