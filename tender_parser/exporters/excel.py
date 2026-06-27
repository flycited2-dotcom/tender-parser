from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from tender_parser.models import TenderRecord


HEADERS = [
    "дата_обнаружения",
    "категория",
    "уверенность",
    "приоритет",
    "название",
    "номер",
    "заказчик",
    "регион",
    "цена",
    "срок_подачи",
    "статус",
    "ссылка",
    "причина_включения",
    "причина_исключения",
    "источник",
]

PRIORITY_ORDER = {"hot": 0, "review": 1, "wide": 2, "excluded": 3, None: 4}


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def sort_for_review(tenders: list[TenderRecord]) -> list[TenderRecord]:
    return sorted(
        tenders,
        key=lambda tender: (
            PRIORITY_ORDER.get(tender.review_priority, 4),
            tender.deadline is None,
            tender.deadline or datetime.max,
            tender.price is None,
            -(tender.price or 0),
            -(tender.discovered_at.timestamp() if tender.discovered_at else 0),
            tender.title,
        ),
    )


def _append_rows(sheet: Worksheet, tenders: list[TenderRecord]) -> None:
    sheet.append(HEADERS)
    for tender in tenders:
        sheet.append(
            [
                _format_dt(tender.discovered_at),
                tender.category or "",
                tender.match_confidence or "",
                tender.review_priority or "",
                tender.title,
                tender.tender_number or "",
                tender.customer or "",
                tender.region or "",
                tender.price,
                _format_dt(tender.deadline),
                tender.status or "",
                tender.url,
                tender.include_reason,
                tender.exclude_reason,
                tender.source,
            ]
        )
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(
            max(len(str(cell.value or "")) for cell in column) + 2, 60
        )


def export_excel(
    hot: list[TenderRecord],
    review: list[TenderRecord],
    wide: list[TenderRecord],
    excluded: list[TenderRecord],
    output_path: Path,
    *,
    new_tenders: list[TenderRecord] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    hot_sheet = workbook.active
    hot_sheet.title = "Горячие"
    _append_rows(hot_sheet, sort_for_review(hot))
    review_sheet = workbook.create_sheet("На проверку")
    _append_rows(review_sheet, sort_for_review(review))
    wide_sheet = workbook.create_sheet("Широкий хвост")
    _append_rows(wide_sheet, sort_for_review(wide))
    excluded_sheet = workbook.create_sheet("Отсеянные")
    _append_rows(excluded_sheet, sort_for_review(excluded))
    if new_tenders is not None:
        new_sheet = workbook.create_sheet("Новые", 0)
        _append_rows(new_sheet, sort_for_review(new_tenders))
    workbook.save(output_path)
    return output_path
