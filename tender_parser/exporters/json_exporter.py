from __future__ import annotations

import json
from datetime import datetime
from math import isfinite
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult


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
        "price": tender.price if tender.price is not None and isfinite(tender.price) else None,
        "deadline": _format_dt(tender.deadline),
        "status": tender.status,
        "published_at": _format_dt(tender.published_at),
        "discovered_at": _format_dt(tender.discovered_at),
        "filter_status": tender.filter_status,
        "match_confidence": tender.match_confidence,
        "review_priority": tender.review_priority,
        "category": tender.category,
        "include_reason": tender.include_reason,
        "exclude_reason": tender.exclude_reason,
        "matched_terms": tender.matched_terms,
        "detail_status": tender.detail_status,
        "document_matches": tender.document_matches,
        "delivery_region_evidence": tender.delivery_region_evidence,
        "source_confidence": tender.source_confidence,
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


def export_run_report(
    report: SourceFetchResult,
    output_path: Path,
    *,
    raw_count: int,
    unique_count: int,
    new_count: int,
    profile: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "summary": {
            "raw_count": raw_count,
            "unique_count": unique_count,
            "new_count": new_count,
        },
        "sources": [
            {
                "source": item.source,
                "status": item.status,
                "found": item.found,
                "elapsed_seconds": item.elapsed_seconds,
                "detail": item.detail,
            }
            for item in report.health
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path
