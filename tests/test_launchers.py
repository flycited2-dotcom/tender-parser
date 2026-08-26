from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_silent_launcher_defaults_to_fast_profile() -> None:
    text = (ROOT / "run_tender_parser_silent.bat").read_text(encoding="utf-8")

    assert "set \"TENDER_PARSER_PROFILE=fast\"" in text
    assert "python -m tender_parser run --profile %TENDER_PARSER_PROFILE%" in text
    assert "exit /b %parser_exit_code%" in text


def test_daily_scheduler_accepts_profile_argument() -> None:
    text = (ROOT / "Настроить_ежедневный_запуск.ps1").read_text(encoding="utf-8")

    assert "[ValidateSet('full', 'fast', 'local', 'rts')]" in text
    assert "[string]$Profile = 'fast'" in text
    assert "-Profile $Profile -ScheduleTime $Time" in text
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "-RestartCount 3" in text


def test_workday_scheduler_configures_four_collection_times() -> None:
    text = (ROOT / "Настроить_рабочие_запуски.ps1").read_text(encoding="utf-8")

    for value in ("08:00", "11:00", "15:00", "18:00"):
        assert value in text
    assert "StartWhenAvailable" in text
    assert "RestartCount 3" in text
    assert "ScheduleTime $time" in text


def test_resilient_runner_guards_duplicate_and_tracks_success() -> None:
    text = (ROOT / "run_tender_parser_resilient.ps1").read_text(encoding="utf-8")

    assert "TenderParserDailyGuard" in text
    assert "scheduler_state.json" in text
    assert "Successful cycle already exists" in text
    assert "Task Scheduler will retry" in text


def test_google_and_telegram_setup_helpers_are_present() -> None:
    google_text = (ROOT / "Настроить_Google_Sheets.ps1").read_text(encoding="utf-8")
    telegram_text = (ROOT / "Настроить_Telegram_бот.ps1").read_text(encoding="utf-8")

    assert "service_account" in google_text
    assert "GOOGLE_SHEETS_ENABLED" in google_text
    assert "New-ScheduledTaskTrigger -AtLogOn" in telegram_text
    assert "Tender Parser Command Bot" in telegram_text
    assert "RestartCount 10" in telegram_text


def test_personal_telegram_agent_has_a_distinct_scheduled_task() -> None:
    text = (ROOT / "Настроить_личного_Telegram_агента.ps1").read_text(
        encoding="utf-8"
    )

    assert "Tender Personal Telegram Agent" in text
    assert "-m tender_parser.telegram_agent" in text
    assert "RestartCount 10" in text


def test_personal_agent_migration_keeps_bot_tokens_separate() -> None:
    text = (ROOT / "Мигрировать_личного_Telegram_агента.ps1").read_text(
        encoding="utf-8"
    )

    assert "TELEGRAM_AGENT_BOT_TOKEN" in text
    assert "TELEGRAM_AGENT_ALLOWED_USER_IDS" in text
    assert "before-agent-migration" in text
    assert "telegram_codex_session.json" in text


def test_eat_setup_helper_writes_ignored_env_file() -> None:
    text = (ROOT / "Настроить_EAT_env.ps1").read_text(encoding="utf-8")

    assert "Set-EnvValue -Key 'EAT_API_TOKEN' -Value $ApiToken" in text
    assert "Set-EnvValue -Key 'EAT_EXT_SYSTEM' -Value $ExtSystem" in text
    assert "without changing other settings" in text
    assert "check-env" in text


def test_rts_accumulator_launchers_call_expected_commands() -> None:
    add_text = (ROOT / "Добавить_страницу_RTS.bat").read_text(encoding="utf-8")
    report_text = (ROOT / "Отчет_по_накопленному_RTS.bat").read_text(encoding="utf-8")

    assert "python -m tender_parser rts-add-page" in add_text
    assert "python -m tender_parser run --profile rts-accumulated" in report_text
    assert "exports\\latest.html" in report_text
    watch_text = (ROOT / "Автосбор_RTS_кабинета.bat").read_text(encoding="utf-8")
    assert "python -m tender_parser rts-watch" in watch_text
    poisk_text = (ROOT / "Поиск_RTS_другие_площадки.bat").read_text(encoding="utf-8")
    assert "rts-tender.ru/poisk/search?id=7a2edb26-ab8d-4fee-86b4-56514059add7" in poisk_text
    assert "223.rts-tender.ru/supplier/auction/Trade/Search.aspx" in poisk_text
    assert "agregatoreat.ru/lk/supplier/eat/purchases/active/all" in poisk_text
    assert 'call "%~dp0open_gpb_yandex.bat"' in poisk_text
    assert "lk.roseltorg.ru" in poisk_text
    assert "44.sberbank-ast.ru/tradezone/Supplier/ESPurchaseList.aspx" in poisk_text
    assert "utp.sberbank-ast.ru/Trade/List/BidListClose" in poisk_text
    assert "C:\\RTSBrowser\\rts-chromium.exe" in poisk_text
    assert "--remote-debugging-port=9222" in poisk_text
    assert "python -m tender_parser rts-watch" in poisk_text


def test_rts_cabinet_launchers_use_isolated_browser_profile() -> None:
    open_text = (ROOT / "Открыть_RTS_кабинет_Chrome.bat").read_text(encoding="utf-8")
    collect_text = (ROOT / "Собрать_RTS_кабинет.bat").read_text(encoding="utf-8")

    assert "--remote-debugging-address=127.0.0.1" in open_text
    assert "--remote-debugging-port=9222" in open_text
    assert "RTSCollectorProfile" in open_text
    assert "rts-chromium.exe" in open_text
    assert "agregatoreat.ru/lk/supplier/eat/purchases/active/all" in open_text
    assert 'call "%~dp0open_gpb_yandex.bat"' in open_text
    assert "lk.roseltorg.ru" in open_text
    assert "44.sberbank-ast.ru/tradezone/Supplier/ESPurchaseList.aspx" in open_text
    assert "utp.sberbank-ast.ru/Trade/List/BidListClose" in open_text
    assert "python -m tender_parser run --profile rts-cabinet" in collect_text


def test_gpb_cabinet_uses_yandex_browser_for_cryptopro_compatibility() -> None:
    helper_text = (ROOT / "open_gpb_yandex.bat").read_text(encoding="utf-8")

    assert "YandexBrowser\\Application\\browser.exe" in helper_text
    assert "etp.gpb.ru/#log/maillist/223" in helper_text
    assert 'start "ETP GPB - Yandex Browser"' in helper_text

    for launcher_name in (
        "Открыть_RTS_кабинет_Chrome.bat",
        "Поиск_RTS_другие_площадки.bat",
    ):
        launcher_text = (ROOT / launcher_name).read_text(encoding="utf-8")
        chromium_line = next(
            line for line in launcher_text.splitlines() if 'start "Tender Collector Browser"' in line
        )
        assert "etp.gpb.ru" not in chromium_line


def test_isolated_rts_task_has_hard_timeout_and_scheduler_retries() -> None:
    runner = (ROOT / "run_rts_background.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "Настроить_фоновый_RTS.ps1").read_text(encoding="utf-8")
    daily_installer = (ROOT / "Настроить_ежедневный_запуск.ps1").read_text(
        encoding="utf-8"
    )

    assert "TenderParserRtsBackgroundGuard" in runner
    assert "rts-refresh" in runner
    assert "WaitForExit($TimeoutMinutes * 60 * 1000)" in runner
    assert "$process.WaitForExit()" in runner
    assert "rts_background_state.json" in runner
    assert "treating the run as failed" in runner
    assert "Stop-Process -Id $process.Id -Force" in runner
    assert "-StartWhenAvailable" in installer
    assert "-RunOnlyIfNetworkAvailable" in installer
    assert "-RestartCount 3" in installer
    assert "-RestartInterval (New-TimeSpan -Minutes 30)" in installer
    assert "-MultipleInstances IgnoreNew" in installer
    for task_installer in (installer, daily_installer):
        assert "New-ScheduledTaskPrincipal" in task_installer
        assert "-UserId 'SYSTEM'" in task_installer
        assert "-LogonType ServiceAccount" in task_installer
        assert "-Principal $principal" in task_installer


def test_tender_agent_has_one_click_hidden_and_visible_launchers() -> None:
    hidden = (ROOT / "Тендерный_агент.vbs").read_text(encoding="utf-8")
    visible = (ROOT / "Открыть_тендерного_агента.bat").read_text(encoding="utf-8")

    assert "pythonw -m tender_parser control-center --open-browser" in hidden
    assert "python -m tender_parser control-center --open-browser" in visible
