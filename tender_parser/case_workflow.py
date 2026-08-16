from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tender_parser.alternative_search import build_alternative_tasks, export_alternative_search
from tender_parser.case_preflight import PreflightResult, analyze_case_documents, export_preflight
from tender_parser.case_report import export_case_report
from tender_parser.supplier_search import (
    LineSearchResult,
    SupplierProductGateway,
    TenderProductApiGateway,
    export_supplier_search,
    search_case_products,
)
from tender_parser.supplier_registry import list_supplier_requests, list_suppliers
from tender_parser.tender_case import CaseEconomics, LineItem, calculate_case, load_case


OFFER_HEADERS = [
    "line_id",
    "supplier",
    "sku",
    "product_name",
    "unit_cost_gross",
    "compliance_status",
    "selected",
    "stock",
    "lead_days",
    "vat_rate",
    "source_url",
    "evidence",
    "notes",
]
EXPENSE_HEADERS = ["category", "description", "amount_gross", "vat_rate", "vat_reclaimable", "confirmed", "notes"]


def run_case_workflow(
    case_dir: Path,
    *,
    gateway: SupplierProductGateway | None = None,
    limit_per_item: int = 10,
) -> dict[str, object]:
    started_at = datetime.now().astimezone()
    output_dir = case_dir / "output"
    preflight = analyze_case_documents(case_dir)
    preflight_outputs = export_preflight(preflight, output_dir)
    metadata_updated = _update_case_metadata(case_dir, preflight)
    items_promoted = promote_item_candidates(case_dir, preflight)
    _, items, _, _ = load_case(case_dir)

    supplier_error = ""
    supplier_results: list[LineSearchResult] = []
    if items:
        try:
            active_gateway = gateway or TenderProductApiGateway.from_environment()
            supplier_results = search_case_products(case_dir, active_gateway, limit_per_item=limit_per_item)
        except (OSError, ValueError) as exc:
            supplier_error = str(exc)
            supplier_results = [
                LineSearchResult(
                    line_id=item.line_id,
                    item_name=item.name,
                    required_specs=item.required_specs,
                    query="",
                    total_found=0,
                    error=supplier_error,
                )
                for item in items
            ]
    supplier_outputs = export_supplier_search(supplier_results, output_dir)
    alternative_tasks = build_alternative_tasks(items, supplier_results)
    alternative_outputs = export_alternative_search(alternative_tasks, output_dir)

    completed_at = datetime.now().astimezone()
    summary = {
        "case_id": case_dir.name,
        "status": "completed",
        "started_at": started_at.isoformat(timespec="seconds"),
        "completed_at": completed_at.isoformat(timespec="seconds"),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 2),
        "documents": len(preflight.documents),
        "items": len(items),
        "items_promoted": items_promoted,
        "metadata_updated": metadata_updated,
        "blockers": sum(finding.severity == "blocker" for finding in preflight.findings),
        "risks": sum(finding.severity == "risk" for finding in preflight.findings),
        "supplier_products": sum(len(result.products) for result in supplier_results),
        "supplier_error": supplier_error,
        "alternative_required": sum(task.status == "required" for task in alternative_tasks),
        "alternative_verify": sum(task.status == "verify" for task in alternative_tasks),
        "outputs": {
            **{f"preflight_{key}": str(path) for key, path in preflight_outputs.items()},
            **{f"supplier_{key}": str(path) for key, path in supplier_outputs.items()},
            **{f"alternative_{key}": str(path) for key, path in alternative_outputs.items()},
        },
    }
    (output_dir / "workflow_status.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def promote_item_candidates(case_dir: Path, preflight: PreflightResult) -> bool:
    items_path = case_dir / "items.csv"
    if _has_working_items(items_path) or not preflight.item_candidates:
        return False
    with items_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["line_id", "name", "quantity", "unit", "required_specs", "mandatory"])
        for item in preflight.item_candidates:
            writer.writerow([item.line_id, item.name, item.quantity, item.unit, item.required_specs, "yes"])
    return True


def load_case_dashboard(case_dir: Path) -> dict[str, object]:
    case_payload = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    tender_case, items, offers, expenses = load_case(case_dir)
    economics = calculate_case(tender_case, items, offers, expenses)
    output = case_dir / "output"
    return {
        "case": case_payload,
        "items": [
            {
                "line_id": item.line_id,
                "name": item.name,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "required_specs": item.required_specs,
                "mandatory": item.mandatory,
            }
            for item in items
        ],
        "expenses": [
            {
                "category": expense.category,
                "description": expense.description,
                "amount_gross": str(expense.amount_gross),
                "confirmed": expense.confirmed,
            }
            for expense in expenses
        ],
        "offers": [
            {
                "line_id": offer.line_id,
                "supplier": offer.supplier,
                "sku": offer.sku,
                "product_name": offer.product_name,
                "unit_cost_gross": str(offer.unit_cost_gross),
                "compliance_status": offer.compliance_status,
                "selected": offer.selected,
                "stock": offer.stock,
                "lead_days": offer.lead_days,
                "source_url": offer.source_url,
                "evidence": offer.evidence,
                "notes": offer.notes,
            }
            for offer in offers
        ],
        "economics": _economics_payload(economics),
        "preflight": _read_json(output / "preflight.json", {}),
        "supplier": _read_json(output / "supplier_candidates.json", []),
        "alternatives": _read_json(output / "alternative_search.json", []),
        "alternative_suppliers": list_suppliers(case_dir.parent.parent),
        "supplier_requests": list_supplier_requests(case_dir),
        "workflow": _read_json(output / "workflow_status.json", {}),
    }


def select_supplier_candidate(case_dir: Path, *, line_id: str, sku: str) -> dict[str, object]:
    """Confirm one API candidate for a tender line and refresh the case economics."""
    line_id = line_id.strip()
    sku = sku.strip()
    if not line_id or not sku:
        raise ValueError("Не указаны позиция или артикул товара")
    payload = _read_json(case_dir / "output" / "supplier_candidates.json", [])
    if not isinstance(payload, list):
        raise ValueError("Результат поиска поставщика поврежден; запустите анализ заново")
    line = next((row for row in payload if str(row.get("line_id") or "") == line_id), None)
    products = line.get("products", []) if isinstance(line, dict) else []
    product = next((row for row in products if str(row.get("sku") or "") == sku), None)
    if not isinstance(product, dict):
        raise ValueError("Товар не найден в сохраненном результате поиска")
    status = str(product.get("compliance_status") or "conditional")
    if status == "not_compliant":
        raise ValueError("Нельзя выбрать товар, который не соответствует ТЗ")
    if status not in {"exact", "compliant", "conditional"}:
        status = "conditional"
    try:
        price = Decimal(str(product.get("purchase_price_gross")))
    except (InvalidOperation, TypeError):
        raise ValueError("У товара нет корректной закупочной цены") from None
    if price <= 0:
        raise ValueError("Закупочная цена товара должна быть больше нуля")

    checks = product.get("compliance_checks", [])
    evidence_parts = []
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue
            requirement = str(check.get("requirement") or "Проверка")
            check_status = str(check.get("status") or "unknown")
            reason = str(check.get("reason") or "")
            evidence_parts.append(f"{requirement}: {check_status}" + (f" — {reason}" if reason else ""))
    evidence = "; ".join(evidence_parts) or "Выбрано владельцем из результата автопоиска"
    stock = str(product.get("stock_status") or ("available" if product.get("is_available") else "unknown"))
    notes = "Выбрано в панели тендерного агента."
    if status == "conditional":
        notes += " Условное соответствие: перед заявкой подтвердить характеристики документом производителя."
    if not product.get("is_available"):
        notes += " Наличие и срок поставки требуют подтверждения."
    row = {
        "line_id": line_id,
        "supplier": "Основной поставщик (API)",
        "sku": sku,
        "product_name": str(product.get("name") or sku),
        "unit_cost_gross": str(price),
        "compliance_status": status,
        "selected": "yes",
        "stock": stock,
        "lead_days": "" if product.get("delivery_days") is None else str(product.get("delivery_days")),
        "vat_rate": "0.22",
        "source_url": str(product.get("product_url") or ""),
        "evidence": evidence,
        "notes": notes,
    }
    offers_path = case_dir / "offers.csv"
    rows = _read_csv(offers_path)
    replaced = False
    for existing in rows:
        if str(existing.get("line_id") or "").strip() == line_id:
            existing["selected"] = "no"
        if (
            str(existing.get("line_id") or "").strip() == line_id
            and str(existing.get("supplier") or "").strip() == row["supplier"]
            and str(existing.get("sku") or "").strip() == sku
        ):
            existing.update(row)
            replaced = True
    if not replaced:
        rows.append(row)
    _write_dict_csv_atomic(offers_path, OFFER_HEADERS, rows)
    economics, report_error = _recalculate_and_export(case_dir)
    return {
        "selected": {"line_id": line_id, "sku": sku, "product_name": row["product_name"]},
        "economics": _economics_payload(economics),
        "report_error": report_error,
    }


def clear_selected_offer(case_dir: Path, *, line_id: str) -> dict[str, object]:
    line_id = line_id.strip()
    if not line_id:
        raise ValueError("Не указана позиция закупки")
    offers_path = case_dir / "offers.csv"
    rows = _read_csv(offers_path)
    kept_rows = []
    changed = False
    for row in rows:
        is_selected_line = (
            str(row.get("line_id") or "").strip() == line_id
            and str(row.get("selected") or "").strip().lower() in {"1", "true", "yes", "да", "+"}
        )
        if is_selected_line:
            changed = True
            continue
        kept_rows.append(row)
    if changed:
        _write_dict_csv_atomic(offers_path, OFFER_HEADERS, kept_rows)
    economics, report_error = _recalculate_and_export(case_dir)
    return {
        "cleared": changed,
        "line_id": line_id,
        "economics": _economics_payload(economics),
        "report_error": report_error,
    }


def update_case_economics(case_dir: Path, values: dict[str, object]) -> dict[str, object]:
    """Update the owner-confirmed commercial inputs used by the calculation."""
    case_path = case_dir / "case.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    for field in ("nmck", "planned_bid"):
        if field in values:
            parsed = _optional_nonnegative_decimal(values.get(field), label=field)
            payload[field] = None if parsed is None else str(parsed)
    for field in ("region", "delivery_address"):
        if field in values:
            payload[field] = str(values.get(field) or "").strip()
    for field in ("payment_days", "delivery_days"):
        if field in values:
            raw = str(values.get(field) or "").strip()
            if not raw:
                payload[field] = None if field == "delivery_days" else 7
            else:
                try:
                    parsed_days = int(raw)
                except ValueError:
                    raise ValueError(f"Поле {field} должно содержать целое число дней") from None
                if parsed_days < 0:
                    raise ValueError(f"Поле {field} не может быть отрицательным")
                payload[field] = parsed_days
    case_temp = case_path.with_suffix(".json.tmp")
    case_temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    case_temp.replace(case_path)

    expense_fields = {
        "delivery": ("delivery_cost", "Доставка"),
        "unloading": ("unloading_cost", "Разгрузка"),
        "installation": ("installation_cost", "Монтаж/пусконаладка"),
    }
    expenses_path = case_dir / "expenses.csv"
    rows = _read_csv(expenses_path)
    for category, (field, description) in expense_fields.items():
        if field not in values:
            continue
        amount = _optional_nonnegative_decimal(values.get(field), label=field) or Decimal("0")
        row = next((candidate for candidate in rows if candidate.get("category") == category), None)
        updated = {
            "category": category,
            "description": description,
            "amount_gross": str(amount),
            "vat_rate": "0",
            "vat_reclaimable": "no",
            "confirmed": "yes" if amount > 0 else "no",
            "notes": "Сумма введена владельцем в панели",
        }
        if row is None:
            rows.append(updated)
        else:
            row.update(updated)
    _write_dict_csv_atomic(expenses_path, EXPENSE_HEADERS, rows)
    economics, report_error = _recalculate_and_export(case_dir)
    return {"economics": _economics_payload(economics), "report_error": report_error}


def list_case_dashboards(base_dir: Path) -> list[dict[str, object]]:
    cases_dir = base_dir / "cases"
    if not cases_dir.exists():
        return []
    cases = []
    for case_dir in cases_dir.iterdir():
        case_path = case_dir / "case.json"
        if not case_dir.is_dir() or not case_path.exists():
            continue
        try:
            payload = json.loads(case_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        workflow = _read_json(case_dir / "output" / "workflow_status.json", {})
        cases.append(
            {
                "case_id": case_dir.name,
                "title": str(payload.get("title") or case_dir.name),
                "tender_number": str(payload.get("tender_number") or ""),
                "nmck": payload.get("nmck"),
                "updated_at": datetime.fromtimestamp(case_path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "workflow": workflow,
            }
        )
    return sorted(cases, key=lambda item: str(item["updated_at"]), reverse=True)


def _has_working_items(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return any((row.get("name") or "").strip() for row in csv.DictReader(handle, delimiter=";"))


def _update_case_metadata(case_dir: Path, preflight: PreflightResult) -> bool:
    path = case_dir / "case.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    if payload.get("nmck") in {None, ""}:
        values = preflight.metadata_candidates.get("nmck", [])
        if values:
            try:
                payload["nmck"] = float(Decimal(values[0]["value"].replace(" ", "").replace(",", ".")))
                changed = True
            except (InvalidOperation, KeyError):
                pass
    if not str(payload.get("tender_number") or "").strip():
        match = re.fullmatch(r"\d{10,25}", str(payload.get("title") or "").strip())
        if match:
            payload["tender_number"] = match.group()
            changed = True
    if changed:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _write_dict_csv_atomic(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _optional_nonnegative_decimal(value: object, *, label: str) -> Decimal | None:
    text = str(value or "").strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Поле {label} содержит некорректную сумму") from None
    if parsed < 0:
        raise ValueError(f"Поле {label} не может быть отрицательным")
    return parsed


def _recalculate_and_export(case_dir: Path) -> tuple[CaseEconomics, str]:
    tender_case, items, offers, expenses = load_case(case_dir)
    economics = calculate_case(tender_case, items, offers, expenses)
    try:
        export_case_report(
            tender_case,
            economics,
            offers,
            expenses,
            case_dir,
            case_dir / "output" / "case_report.xlsx",
        )
        report_error = ""
    except OSError as exc:
        report_error = f"Расчет сохранен, но Excel-отчет не обновлён: {exc}"
    return economics, report_error


def _economics_payload(economics: object) -> dict[str, object]:
    selected_lines = getattr(economics, "selected_lines", [])
    return {
        "selected_count": sum(line.offer is not None for line in selected_lines),
        "total_lines": len(selected_lines),
        "procurement_gross": str(economics.procurement_gross),
        "expenses_gross": str(economics.expenses_gross),
        "target_price": str(economics.target_price),
        "viable_price": str(economics.viable_price),
        "hard_floor_price": str(economics.hard_floor_price),
        "assessment_price": None if economics.assessment_price is None else str(economics.assessment_price),
        "headroom_to_target": None if economics.headroom_to_target is None else str(economics.headroom_to_target),
        "target_discount_from_nmck": (
            None if economics.target_discount_from_nmck is None else str(economics.target_discount_from_nmck)
        ),
        "viable_discount_from_nmck": (
            None if economics.viable_discount_from_nmck is None else str(economics.viable_discount_from_nmck)
        ),
        "hard_floor_discount_from_nmck": (
            None if economics.hard_floor_discount_from_nmck is None else str(economics.hard_floor_discount_from_nmck)
        ),
        "decision": economics.decision,
        "decision_reason": economics.decision_reason,
        "risks": list(economics.risks),
        "questions": list(economics.questions),
        "entity_scenarios": [
            {
                "entity": scenario.entity,
                "sale_price_gross": str(scenario.sale_price_gross),
                "profit_before_income_tax": str(scenario.profit_before_income_tax),
                "estimated_tax": None if scenario.estimated_tax is None else str(scenario.estimated_tax),
                "profit_after_estimated_tax": (
                    None if scenario.profit_after_estimated_tax is None else str(scenario.profit_after_estimated_tax)
                ),
                "profit_rate_on_cost": None if scenario.profit_rate_on_cost is None else str(scenario.profit_rate_on_cost),
            }
            for scenario in economics.entity_scenarios
        ],
    }
