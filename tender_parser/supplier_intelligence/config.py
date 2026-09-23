"""Configuration for the read-only Gmail supplier index."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class MailboxConfig:
    address: str
    token_path: Path


@dataclass(frozen=True)
class SupplierConfig:
    base_dir: Path
    database_path: Path
    client_secret_path: Path
    service_account_path: Path
    spreadsheet_id: str
    mailboxes: tuple[MailboxConfig, ...]
    own_emails: frozenset[str]
    own_domains: frozenset[str]
    ignored_sender_domains: frozenset[str] = frozenset()
    ignored_sender_prefixes: frozenset[str] = frozenset()
    confidence_threshold: float = 0.70
    batch_size: int = 500
    max_retries: int = 3
    attachment_max_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_env(cls, base_dir: Path) -> "SupplierConfig":
        base_dir = base_dir.resolve()
        config_path = base_dir / "tender_parser" / "supplier_intelligence" / "config" / "config.yaml"
        settings = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        settings = settings or {}
        if not isinstance(settings, dict):
            raise ValueError("supplier intelligence config must be a YAML mapping")
        own_emails = {str(value).strip().casefold() for value in settings.get("own_emails", [])}
        own_domains = {str(value).strip().casefold() for value in settings.get("own_domains", [])}
        own_emails.update(_list_env("SUPPLIER_OWN_EMAILS"))
        own_domains.update(_list_env("SUPPLIER_OWN_DOMAINS"))
        accounts: list[MailboxConfig] = []
        suffixes = sorted(
            int(match.group(1))
            for key in os.environ
            if (match := re.fullmatch(r"GMAIL_ACCOUNT_(\d+)", key))
        )
        for number in suffixes:
            address = os.getenv(f"GMAIL_ACCOUNT_{number}", "").strip().casefold()
            if not address:
                continue
            token = os.getenv(f"GMAIL_TOKEN_{number}", "").strip() or f"secrets/gmail-token-{number}.json"
            accounts.append(MailboxConfig(address, _path(base_dir, token)))
            own_emails.add(address)
        if len({item.address for item in accounts}) != len(accounts):
            raise ValueError("GMAIL_ACCOUNT_n contains duplicate accounts")
        threshold = float(os.getenv("SUPPLIER_CONFIDENCE_THRESHOLD", settings.get("confidence_threshold", 0.70)))
        if not 0 < threshold <= 1:
            raise ValueError("SUPPLIER_CONFIDENCE_THRESHOLD must be in (0, 1]")
        return cls(
            base_dir=base_dir,
            database_path=_path(base_dir, os.getenv("SUPPLIER_DB_PATH", "data/supplier_intelligence.db")),
            client_secret_path=_path(base_dir, os.getenv("GMAIL_OAUTH_CLIENT_FILE", "secrets/gmail-client.json")),
            service_account_path=_path(base_dir, os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/google-service-account.json")),
            spreadsheet_id=os.getenv("SUPPLIER_SPREADSHEET_ID", "").strip(),
            mailboxes=tuple(accounts),
            own_emails=frozenset(email for email in own_emails if email),
            own_domains=frozenset(domain for domain in own_domains if domain),
            ignored_sender_domains=frozenset(
                {str(value).strip().casefold() for value in settings.get("ignored_sender_domains", [])}
                | _list_env("SUPPLIER_IGNORED_SENDER_DOMAINS")
            ),
            ignored_sender_prefixes=frozenset(
                {str(value).strip().casefold() for value in settings.get("ignored_sender_prefixes", [])}
                | _list_env("SUPPLIER_IGNORED_SENDER_PREFIXES")
            ),
            confidence_threshold=threshold,
            batch_size=max(1, min(500, int(os.getenv("SUPPLIER_BATCH_SIZE", "500")))),
            max_retries=max(1, int(os.getenv("SUPPLIER_MAX_RETRIES", "3"))),
            attachment_max_bytes=max(0, int(os.getenv("SUPPLIER_ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024)))),
        )


def _path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def _list_env(key: str) -> set[str]:
    return {part.strip().casefold() for part in re.split(r"[,;\n]+", os.getenv(key, "")) if part.strip()}
