from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from time import sleep

import requests

from tender_parser.env import load_env_file
from tender_parser.notifications import NotificationConfig, TelegramNotifier


class TelegramCommandBot:
    def __init__(
        self,
        base_dir: Path,
        config: NotificationConfig,
        session: requests.Session | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.config = config
        self.session = session or requests.Session()
        self.notifier = TelegramNotifier(config, session=self.session)
        self.offset_path = base_dir / "data" / "telegram_update_offset.txt"
        self._refresh_lock = threading.Lock()

    def run_forever(self) -> int:
        if not self.config.enabled:
            print("Telegram bot is not configured")
            return 2
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        self._set_commands()
        offset = self._load_offset()
        while True:
            try:
                response = self.session.get(
                    self._endpoint("getUpdates"),
                    params={
                        "offset": offset,
                        "timeout": 25,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                    timeout=35,
                )
                response.raise_for_status()
                payload = response.json()
                for update in payload.get("result", []):
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    self._handle_update(update)
                    self._save_offset(offset)
            except (requests.RequestException, ValueError, OSError) as exc:
                print(f"Telegram polling error: {exc.__class__.__name__}")
                sleep(10)

    def _handle_update(self, update: dict) -> None:
        callback = update.get("callback_query") or {}
        message = update.get("message") or callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != self.config.chat_id:
            return
        if callback:
            self._answer_callback(str(callback.get("id", "")))
            command = str(callback.get("data", ""))
        else:
            command = str(message.get("text", "")).strip().split("@", 1)[0].casefold()
        if command in {"/start", "/help"}:
            self.notifier.send_text(
                "Управление тендерным парсером:\n"
                "🩺 /status — состояние\n"
                "📎 /report — последний Excel\n"
                "🔄 /fresh — обновить сейчас",
                buttons=True,
            )
        elif command in {"/status", "parser_status", "🩺 состояние"}:
            self.notifier.send_text(self._status_text(), buttons=True)
        elif command in {"/report", "latest_report", "📎 последний excel"}:
            self._send_latest_report()
        elif command in {"/fresh", "refresh_now", "🔄 обновить сейчас"}:
            self._start_refresh()

    def _start_refresh(self) -> None:
        if not self._refresh_lock.acquire(blocking=False):
            self.notifier.send_text("⏳ Обновление уже выполняется.", buttons=True)
            return
        self.notifier.send_text("🔄 Запустил свежий сбор. По завершении пришлю Excel.")
        thread = threading.Thread(target=self._run_refresh, daemon=True)
        thread.start()

    def _run_refresh(self) -> None:
        try:
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            runner = self.base_dir / "run_tender_parser_resilient.ps1"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(runner),
                    "-Profile",
                    "fast",
                    "-ScheduleTime",
                    "08:00",
                    "-Force",
                ],
                cwd=self.base_dir,
                creationflags=flags,
                check=False,
            )
            if completed.returncode != 0:
                self.notifier.send_text(
                    f"⚠️ Сбор завершился с кодом {completed.returncode}. Планировщик повторит попытку."
                )
        finally:
            self._refresh_lock.release()

    def _send_latest_report(self) -> None:
        reports = sorted(
            (self.base_dir / "exports").glob("tenders_*.xlsx"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not reports:
            self.notifier.send_text("Excel-отчёт пока не сформирован.")
            return
        self.notifier.send_document(reports[0], caption="Последний сформированный отчёт")

    def _status_text(self) -> str:
        report_path = self.base_dir / "exports" / "run_report.json"
        if not report_path.is_file():
            return "⚠️ Данных о последнем запуске пока нет."
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "⚠️ Последний отчёт повреждён или недоступен."
        summary = report.get("summary") or {}
        sources = report.get("sources") or []
        good = sum(item.get("status") in {"ok", "empty"} for item in sources)
        return "\n".join(
            [
                "🩺 Состояние тендерного парсера",
                f"Последний запуск: {report.get('generated_at', '—')}",
                f"Профиль: {report.get('profile', '—')}",
                f"Новых: {summary.get('new_count', 0)}",
                f"После дедупликации: {summary.get('unique_count', 0)}",
                f"Источники: {good} из {len(sources)}",
            ]
        )

    def _set_commands(self) -> None:
        try:
            self.session.post(
                self._endpoint("setMyCommands"),
                json={
                    "commands": [
                        {"command": "status", "description": "Состояние парсера"},
                        {"command": "report", "description": "Последний Excel"},
                        {"command": "fresh", "description": "Обновить сейчас"},
                    ]
                },
                timeout=self.config.timeout_seconds,
            ).raise_for_status()
        except requests.RequestException:
            pass

    def _answer_callback(self, callback_id: str) -> None:
        if not callback_id:
            return
        try:
            self.session.post(
                self._endpoint("answerCallbackQuery"),
                json={"callback_query_id": callback_id},
                timeout=self.config.timeout_seconds,
            ).raise_for_status()
        except requests.RequestException:
            pass

    def _endpoint(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

    def _load_offset(self) -> int:
        try:
            return int(self.offset_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0

    def _save_offset(self, offset: int) -> None:
        self.offset_path.write_text(str(offset), encoding="utf-8")


def main() -> int:
    base_dir = Path(__file__).resolve().parents[1]
    load_env_file(base_dir / ".env")
    load_env_file(base_dir / ".env.local")
    return TelegramCommandBot(base_dir, NotificationConfig.from_env()).run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
