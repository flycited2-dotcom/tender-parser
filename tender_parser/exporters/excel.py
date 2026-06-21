from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from tender_parser.models import TenderRecord


HEADERS = [
    "дата_обнаружения",
    "категория",
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


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _append_rows(sheet: Worksheet, tenders: list[TenderRecord]) -> None:
    sheet.append(HEADERS)
    for tender in tenders:
        sheet.append(
            [
                _format_dt(tender.discovered_at),
                tender.category or "",
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
    matched: list[TenderRecord],
    review: list[TenderRecord],
    excluded: list[TenderRecord],
    output_path: Path,
    *,
    new_tenders: list[TenderRecord] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    matched_sheet = workbook.active
    matched_sheet.title = "Подходящие"
    _append_rows(matched_sheet, matched)
    review_sheet = workbook.create_sheet("На проверку")
    _append_rows(review_sheet, review)
    excluded_sheet = workbook.create_sheet("Отсеянные")
    _append_rows(excluded_sheet, excluded)
    if new_tenders is not None:
        new_sheet = workbook.create_sheet("Новые", 0)
        _append_rows(new_sheet, new_tenders)
    workbook.save(output_path)
    return output_path
