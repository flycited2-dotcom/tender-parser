from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from tender_parser.product_intelligence import (
    alternative_search_links,
    build_search_queries,
    extract_device_models,
    oem_references,
)
from tender_parser.supplier_search import LineSearchResult
from tender_parser.tender_case import LineItem


@dataclass(frozen=True)
class AlternativeSearchTask:
    line_id: str
    item_name: str
    quantity: str
    unit: str
    status: str
    reason: str
    device_models: tuple[str, ...]
    oem_parts: tuple[str, ...]
    search_queries: tuple[str, ...]
    evidence_urls: tuple[str, ...]
    search_links: tuple[dict[str, str], ...]
    request_subject: str
    request_text: str


def build_alternative_tasks(
    items: list[LineItem], results: list[LineSearchResult]
) -> list[AlternativeSearchTask]:
    by_line = {result.line_id: result for result in results}
    tasks: list[AlternativeSearchTask] = []
    for item in items:
        result = by_line.get(item.line_id)
        products = result.products if result else []
        confirmed_available = [
            product
            for product in products
            if product.compliance_status in {"exact", "compliant"}
            and product.purchase_price_gross is not None
            and (product.is_available or product.stock_status in {"plenty", "available", "low"})
        ]
        conditional_available = [
            product
            for product in products
            if product.compliance_status == "conditional"
            and product.purchase_price_gross is not None
            and (product.is_available or product.stock_status in {"plenty", "available", "low"})
        ]
        if confirmed_available:
            status = "backup"
            reason = "Основной поставщик подходит; альтернативы нужны для сравнения цены и резерва наличия."
        elif conditional_available:
            status = "verify"
            reason = "Есть товар в наличии, но соответствие подтверждено не полностью."
        else:
            status = "required"
            reason = "У основного поставщика нет подтвержденного товара в наличии."
        references = oem_references(item.name, item.required_specs)
        parts = tuple(dict.fromkeys(part for reference in references for part in reference.parts))
        evidence = tuple(dict.fromkeys(reference.evidence_url for reference in references))
        queries = build_search_queries(item.name, item.required_specs)
        subject = f"Запрос цены и наличия: {item.name}, {item.quantity} {item.unit}"
        part_text = ", ".join(parts) if parts else "не определен — подобрать строго по характеристикам"
        request_text = (
            "Добрый день! Просим сообщить цену, наличие и срок поставки товара.\n"
            f"Позиция: {item.name}.\nКоличество: {item.quantity} {item.unit}.\n"
            f"OEM/ориентир: {part_text}.\nТребования: {item.required_specs or 'согласно приложенному ТЗ'}.\n"
            "Просим указать производителя, точный артикул, цену с НДС, срок действия цены, "
            "срок отгрузки и приложить паспорт/спецификацию. Карточка предприятия прилагается."
        )
        tasks.append(
            AlternativeSearchTask(
                line_id=item.line_id,
                item_name=item.name,
                quantity=str(item.quantity),
                unit=item.unit,
                status=status,
                reason=reason,
                device_models=extract_device_models(item.required_specs),
                oem_parts=parts,
                search_queries=queries,
                evidence_urls=evidence,
                search_links=alternative_search_links(item.name, item.required_specs),
                request_subject=subject,
                request_text=request_text,
            )
        )
    return tasks


def export_alternative_search(tasks: list[AlternativeSearchTask], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "alternative_search.json"
    markdown_path = output_dir / "alternative_search.md"
    requests_path = output_dir / "supplier_requests.csv"
    json_path.write_text(json.dumps([asdict(task) for task in tasks], ensure_ascii=False, indent=2), encoding="utf-8")
    with requests_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            [
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
        )
        for task in tasks:
            if task.status in {"required", "verify"}:
                writer.writerow(
                    [
                        task.line_id,
                        task.status,
                        task.item_name,
                        task.quantity,
                        task.unit,
                        ", ".join(task.oem_parts),
                        "",
                        "",
                        task.request_subject,
                        task.request_text,
                        "",
                        "не отправлен",
                        "",
                        "",
                        "",
                        "",
                    ]
                )
    markdown_path.write_text(_render_markdown(tasks), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path, "requests": requests_path}


def _render_markdown(tasks: list[AlternativeSearchTask]) -> str:
    labels = {"required": "ОБЯЗАТЕЛЬНО", "verify": "ПРОВЕРИТЬ", "backup": "РЕЗЕРВ"}
    lines = ["# Поиск у альтернативных поставщиков", ""]
    for task in tasks:
        lines.extend(
            [
                f"## {task.line_id}. {task.item_name}",
                "",
                f"Статус: **{labels.get(task.status, task.status)}**. {task.reason}",
                f"Модели: {', '.join(task.device_models) or 'не выделены'}.",
                f"OEM/ориентиры: {', '.join(task.oem_parts) or 'не определены'}.",
                f"Поисковые запросы: {', '.join(f'`{query}`' for query in task.search_queries)}.",
            ]
        )
        for link in task.search_links:
            lines.append(f"- [{link['label']}]({link['url']}) — `{link['query']}`")
        lines.append("")
    return "\n".join(lines)
