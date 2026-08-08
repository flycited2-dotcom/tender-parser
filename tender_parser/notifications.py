from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import requests

from tender_parser.models import TenderRecord


NotificationStatus = Literal["disabled", "no_new", "sent", "error"]
TELEGRAM_MESSAGE_LIMIT = 3900


@dataclass(frozen=True)
class NotificationConfig:
    bot_token: str = ""
    chat_id: str = ""
    max_items: int = 10
    timeout_seconds: int = 10

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @classmethod
    def from_env(cls) -> "NotificationConfig":
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            max_items=_positive_int(os.getenv("TELEGRAM_MAX_ITEMS", ""), default=10),
            timeout_seconds=_positive_int(
                os.getenv("TELEGRAM_TIMEOUT_SECONDS", ""), default=10
            ),
        )


@dataclass(frozen=True)
class NotificationResult:
    status: NotificationStatus
    sent_count: int = 0
    detail: str = ""


class TelegramNotifier:
    def __init__(
        self,
        config: NotificationConfig,
        session: requests.Session | object | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()

    def notify(self, tenders: list[TenderRecord]) -> NotificationResult:
        if not self.config.enabled:
            return NotificationResult(status="disabled", detail="Telegram не настроен")
        if not tenders:
            return NotificationResult(status="no_new", detail="Нет новых закупок")

        endpoint = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": build_notification_digest(tenders, max_items=self.config.max_items),
            "disable_web_page_preview": True,
        }
        try:
            response = self.session.post(  # type: ignore[attr-defined]
                endpoint,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # Не включаем URL запроса: в нём находится токен Telegram-бота.
            return NotificationResult(status="error", detail=exc.__class__.__name__)
        return NotificationResult(status="sent", sent_count=len(tenders), detail="Отправлено")


def build_notification_digest(tenders: list[TenderRecord], *, max_items: int = 10) -> str:
    if not tenders:
        return "Новых подходящих закупок нет."

    shown = tenders[: max(1, max_items)]
    lines = [f"Новых подходящих закупок: {len(tenders)}", ""]
    for index, tender in enumerate(shown, start=1):
        lines.extend(_tender_lines(tender, index=index))
    remaining = len(tenders) - len(shown)
    if remaining > 0:
        lines.append(f"Ещё закупок: {remaining}. Полный список — в exports/new_tenders.json")
    return _truncate("\n".join(lines).strip(), TELEGRAM_MESSAGE_LIMIT)


def export_notification_digest(tenders: list[TenderRecord], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_notification_digest(tenders) + "\n", encoding="utf-8")
    return output_path


def _tender_lines(tender: TenderRecord, *, index: int) -> list[str]:
    priority = {"hot": "🔥", "review": "🔎", "wide": "📌"}.get(
        tender.review_priority or "", "📌"
    )
    lines = [f"{priority} {index}. {_single_line(tender.title, limit=240)}"]
    facts = [
        _single_line(tender.region or "", limit=100),
        _format_price(tender.price),
        _format_deadline(tender.deadline),
    ]
    useful_facts = [fact for fact in facts if fact]
    if useful_facts:
        lines.append(" · ".join(useful_facts))
    if _is_safe_web_url(tender.url):
        lines.append(tender.url)
    lines.append("")
    return lines


def _format_price(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f} ₽".replace(",", " ")


def _format_deadline(value: datetime | None) -> str:
    return value.strftime("до %d.%m.%Y %H:%M") if value else ""


def _is_safe_web_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _single_line(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
