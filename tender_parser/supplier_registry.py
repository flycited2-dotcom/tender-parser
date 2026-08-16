from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from tender_parser.tender_case import slugify_case_id


SUPPLIER_HEADERS = ["supplier_id", "name", "email", "phone", "website", "categories", "active", "notes"]
REQUEST_HEADERS = [
    "line_id",
    "status",
    "item_name",
    "quantity",
    "unit",
    "oem_parts",
    "supplier",
    "email",
    "request_subject",
    "request_text",
    "sent_at",
    "response_status",
    "response_price",
    "response_stock",
    "response_lead_days",
    "notes",
]
REQUEST_STATUSES = {"не отправлен", "подготовлен", "отправлен", "ответ получен", "отказ", "нет ответа"}


def list_suppliers(base_dir: Path) -> list[dict[str, str]]:
    rows = _read_csv(_registry_path(base_dir))
    return [row for row in rows if str(row.get("active") or "yes").strip().lower() not in {"no", "false", "0"}]


def add_supplier(base_dir: Path, values: dict[str, object]) -> dict[str, str]:
    name = str(values.get("name") or "").strip()
    if not name:
        raise ValueError("Укажите название поставщика")
    email = str(values.get("email") or "").strip()
    phone = str(values.get("phone") or "").strip()
    website = str(values.get("website") or "").strip()
    if not any((email, phone, website)):
        raise ValueError("Укажите email, телефон или сайт поставщика")
    path = _registry_path(base_dir)
    rows = _read_csv(path)
    base_id = slugify_case_id(name).lower()
    supplier_id = base_id
    existing_ids = {row.get("supplier_id") for row in rows}
    index = 2
    while supplier_id in existing_ids:
        supplier_id = f"{base_id}-{index}"
        index += 1
    row = {
        "supplier_id": supplier_id,
        "name": name,
        "email": email,
        "phone": phone,
        "website": website,
        "categories": str(values.get("categories") or "").strip(),
        "active": "yes",
        "notes": str(values.get("notes") or "").strip(),
    }
    rows.append(row)
    _write_csv_atomic(path, SUPPLIER_HEADERS, rows)
    return row


def list_supplier_requests(case_dir: Path) -> list[dict[str, str]]:
    return _read_csv(case_dir / "output" / "supplier_requests.csv")


def assign_supplier_request(
    base_dir: Path,
    case_dir: Path,
    *,
    line_id: str,
    supplier_id: str,
    response_status: str = "подготовлен",
) -> dict[str, str]:
    line_id = line_id.strip()
    supplier_id = supplier_id.strip()
    if not line_id or not supplier_id:
        raise ValueError("Не указаны позиция или поставщик")
    if response_status not in REQUEST_STATUSES:
        raise ValueError("Неизвестный статус запроса КП")
    supplier = next((row for row in list_suppliers(base_dir) if row.get("supplier_id") == supplier_id), None)
    if supplier is None:
        raise ValueError("Поставщик не найден в реестре")
    path = case_dir / "output" / "supplier_requests.csv"
    rows = _read_csv(path)
    template = next((row for row in rows if row.get("line_id") == line_id), None)
    if template is None:
        raise ValueError("Для этой позиции нет шаблона запроса; запустите анализ")
    row = next(
        (candidate for candidate in rows if candidate.get("line_id") == line_id and candidate.get("supplier") == supplier["name"]),
        None,
    )
    if row is None:
        empty = next(
            (candidate for candidate in rows if candidate.get("line_id") == line_id and not candidate.get("supplier")),
            None,
        )
        if empty is not None:
            row = empty
        else:
            row = dict(template)
            rows.append(row)
    row["supplier"] = supplier["name"]
    row["email"] = supplier.get("email", "")
    row["response_status"] = response_status
    if response_status == "отправлен" and not row.get("sent_at"):
        row["sent_at"] = datetime.now().astimezone().isoformat(timespec="minutes")
    _write_csv_atomic(path, REQUEST_HEADERS, rows)
    return row


def update_supplier_request(
    case_dir: Path,
    *,
    line_id: str,
    supplier: str,
    values: dict[str, object],
) -> dict[str, str]:
    path = case_dir / "output" / "supplier_requests.csv"
    rows = _read_csv(path)
    row = next(
        (
            candidate
            for candidate in rows
            if candidate.get("line_id") == line_id.strip() and candidate.get("supplier") == supplier.strip()
        ),
        None,
    )
    if row is None:
        raise ValueError("Запрос к этому поставщику не найден")
    status = str(values.get("response_status") or row.get("response_status") or "подготовлен").strip()
    if status not in REQUEST_STATUSES:
        raise ValueError("Неизвестный статус запроса КП")
    row["response_status"] = status
    for field in ("response_price", "response_stock", "response_lead_days", "notes"):
        if field in values:
            row[field] = str(values.get(field) or "").strip()
    if status == "отправлен" and not row.get("sent_at"):
        row["sent_at"] = datetime.now().astimezone().isoformat(timespec="minutes")
    _write_csv_atomic(path, REQUEST_HEADERS, rows)
    return row


def _registry_path(base_dir: Path) -> Path:
    return base_dir / "data" / "alternative_suppliers.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _write_csv_atomic(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
