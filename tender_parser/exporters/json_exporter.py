from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord


def _format_dt(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _to_dict(tender: TenderRecord) -> dict[str, object]:
    return {
        "title": tender.title,
        "url": tender.url,
        "source": tender.source,
        "tender_number": tender.tender_number,
        "customer": tender.customer,
        "region": tender.region,
        "price": tender.price,
        "deadline": _format_dt(tender.deadline),
        "status": tender.status,
        "published_at": _format_dt(tender.published_at),
        "discovered_at": _format_dt(tender.discovered_at),
        "filter_status": tender.filter_status,
        "category": tender.category,
        "include_reason": tender.include_reason,
        "exclude_reason": tender.exclude_reason,
        "matched_terms": tender.matched_terms,
    }


def export_json(matched: list[TenderRecord], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(matched),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": [_to_dict(tender) for tender in matched],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path
