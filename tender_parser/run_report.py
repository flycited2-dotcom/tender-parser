from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from tender_parser.models import TenderRecord


SourceStatus = Literal[
    "ok", "empty", "suspect_empty", "skipped", "partial", "blocked", "timeout", "ssl_error", "error"
]

SOURCE_NAME_ALIASES = {
    "EtpGpbApiSource": "etp-gpb",
    "RoseltorgSource": "roseltorg",
    "ZakazRfSource": "zakazrf",
    "SberbankAstSource": "sberbank-ast",
    "TenderProSource": "tender-pro",
    "Torgi82Source": "torgi82",
    "B2BCenterSource": "b2b-center",
    "EatIntegrationSource": "eat-berezka",
    "EisZakupkiSource": "eis-zakupki",
    "RostenderSource": "rostender",
}


def canonical_source_name(source: str) -> str:
    """Единый ID источника для health-отчёта и строк реестра."""
    return SOURCE_NAME_ALIASES.get(source, source)


@dataclass(frozen=True)
class SourceHealth:
    source: str
    status: SourceStatus
    found: int
    elapsed_seconds: float
    detail: str = ""


@dataclass
class SourceFetchResult:
    tenders: list[TenderRecord] = field(default_factory=list)
    health: list[SourceHealth] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_previous_profile(path: Path) -> str | None:
    """Профиль прошлого запуска: baseline suspect_empty сравним только внутри профиля."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    profile = payload.get("profile") if isinstance(payload, dict) else None
    return profile if isinstance(profile, str) else None


def load_previous_counts(path: Path) -> dict[str, int]:
    """Читает число карточек по источникам из прошлого run_report.json."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list):
        return {}
    counts: dict[str, int] = {}
    for item in sources:
        if isinstance(item, dict) and isinstance(item.get("source"), str) and isinstance(item.get("found"), int):
            source = canonical_source_name(item["source"])
            counts[source] = max(counts.get(source, 0), item["found"])
    return counts


def flag_suspect_empty(health: list[SourceHealth], previous_counts: dict[str, int]) -> list[SourceHealth]:
    """Источник без ошибок и без карточек после непустого прошлого запуска — кандидат на дрейф верстки."""
    flagged: list[SourceHealth] = []
    for item in health:
        previous = previous_counts.get(canonical_source_name(item.source), 0)
        if item.status in {"ok", "empty"} and item.found == 0 and previous > 0:
            detail = f"подозрение на дрейф верстки: прошлый запуск дал {previous} карточек"
            if item.detail:
                detail = f"{item.detail}; {detail}"
            flagged.append(replace(item, status="suspect_empty", detail=detail))
        else:
            flagged.append(item)
    return flagged
