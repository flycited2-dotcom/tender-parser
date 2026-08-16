from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tender_parser.alternative_search import build_alternative_tasks, export_alternative_search
from tender_parser.case_preflight import PreflightResult, analyze_case_documents, export_preflight
from tender_parser.supplier_search import (
    LineSearchResult,
    SupplierProductGateway,
    TenderProductApiGateway,
    export_supplier_search,
    search_case_products,
)
from tender_parser.tender_case import LineItem, load_case


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
    _, items, _, expenses = load_case(case_dir)
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
        "preflight": _read_json(output / "preflight.json", {}),
        "supplier": _read_json(output / "supplier_candidates.json", []),
        "alternatives": _read_json(output / "alternative_search.json", []),
        "workflow": _read_json(output / "workflow_status.json", {}),
    }


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
