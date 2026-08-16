from __future__ import annotations

import html
import json
import mimetypes
import threading
import webbrowser
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from tender_parser.case_workflow import (
    clear_selected_offer,
    list_case_dashboards,
    load_case_dashboard,
    run_case_workflow,
    select_supplier_candidate,
    update_case_economics,
)
from tender_parser.tender_case import initialize_case, slugify_case_id
from tender_parser.supplier_registry import (
    add_supplier,
    assign_supplier_request,
    list_suppliers,
    update_supplier_request,
)


MAX_UPLOAD_BYTES = 250 * 1024 * 1024
JOBS: dict[str, dict[str, object]] = {}
JOBS_LOCK = threading.Lock()


def run_control_center(
    base_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> int:
    url = f"http://{host}:{port}/"
    handler = _handler_for(base_dir.resolve())
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError:
        if open_browser:
            webbrowser.open(url)
            return 0
        raise
    print(f"Тендерный агент: {url}")
    print("Для остановки нажмите Ctrl+C.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _handler_for(base_dir: Path):
    class ControlCenterHandler(BaseHTTPRequestHandler):
        server_version = "TenderControlCenter/1.0"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                selected = parse_qs(parsed.query).get("case", [""])[0]
                self._html(_render_dashboard(base_dir, selected))
                return
            if parsed.path == "/api/cases":
                self._json({"cases": list_case_dashboards(base_dir)})
                return
            if parsed.path == "/api/suppliers":
                self._json({"suppliers": list_suppliers(base_dir)})
                return
            if parsed.path.startswith("/api/cases/"):
                case_id = unquote(parsed.path.removeprefix("/api/cases/")).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                self._json(load_case_dashboard(case_dir))
                return
            if parsed.path.startswith("/api/jobs/"):
                case_id = unquote(parsed.path.removeprefix("/api/jobs/")).strip("/")
                with JOBS_LOCK:
                    job = dict(JOBS.get(case_id, {"status": "idle"}))
                self._json(job)
                return
            if parsed.path.startswith("/case-files/"):
                self._serve_case_file(base_dir, unquote(parsed.path.removeprefix("/case-files/")))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/cases":
                try:
                    case_id = self._create_case(base_dir)
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json({"case_id": case_id, "url": f"/?case={case_id}"}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/suppliers":
                try:
                    supplier = add_supplier(base_dir, self._read_json_body())
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json({"supplier": supplier}, HTTPStatus.CREATED)
                return
            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/offers/select"):
                case_id = unquote(
                    parsed.path.removeprefix("/api/cases/").removesuffix("/offers/select")
                ).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    payload = self._read_json_body()
                    result = select_supplier_candidate(
                        case_dir,
                        line_id=str(payload.get("line_id") or ""),
                        sku=str(payload.get("sku") or ""),
                    )
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json(result)
                return
            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/offers/clear"):
                case_id = unquote(
                    parsed.path.removeprefix("/api/cases/").removesuffix("/offers/clear")
                ).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    payload = self._read_json_body()
                    result = clear_selected_offer(case_dir, line_id=str(payload.get("line_id") or ""))
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json(result)
                return
            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/economics"):
                case_id = unquote(
                    parsed.path.removeprefix("/api/cases/").removesuffix("/economics")
                ).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    result = update_case_economics(case_dir, self._read_json_body())
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json(result)
                return
            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/requests/assign"):
                case_id = unquote(
                    parsed.path.removeprefix("/api/cases/").removesuffix("/requests/assign")
                ).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    payload = self._read_json_body()
                    result = assign_supplier_request(
                        base_dir,
                        case_dir,
                        line_id=str(payload.get("line_id") or ""),
                        supplier_id=str(payload.get("supplier_id") or ""),
                        response_status=str(payload.get("response_status") or "подготовлен"),
                    )
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json({"request": result})
                return
            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/requests/update"):
                case_id = unquote(
                    parsed.path.removeprefix("/api/cases/").removesuffix("/requests/update")
                ).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    payload = self._read_json_body()
                    result = update_supplier_request(
                        case_dir,
                        line_id=str(payload.get("line_id") or ""),
                        supplier=str(payload.get("supplier") or ""),
                        values=payload,
                    )
                except (OSError, ValueError) as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json({"request": result})
                return
            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/run"):
                case_id = unquote(parsed.path.removeprefix("/api/cases/").removesuffix("/run")).strip("/")
                case_dir = _safe_case_dir(base_dir, case_id)
                if case_dir is None:
                    self._json({"error": "Дело не найдено"}, HTTPStatus.NOT_FOUND)
                    return
                with JOBS_LOCK:
                    active = JOBS.get(case_id, {})
                    if active.get("status") == "running":
                        self._json(active, HTTPStatus.ACCEPTED)
                        return
                    JOBS[case_id] = {
                        "status": "running",
                        "message": "Разбираю документы и ищу товары…",
                        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                threading.Thread(target=_workflow_job, args=(case_dir,), daemon=True).start()
                self._json(JOBS[case_id], HTTPStatus.ACCEPTED)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _create_case(self, project_dir: Path) -> str:
            content_length = int(self.headers.get("Content-Length") or 0)
            if content_length <= 0 or content_length > MAX_UPLOAD_BYTES:
                raise ValueError("Размер загрузки недопустим или превышает 250 МБ")
            content_type = self.headers.get("Content-Type") or ""
            if "multipart/form-data" not in content_type:
                raise ValueError("Ожидается форма с документами")
            body = self.rfile.read(content_length)
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
            )
            fields: dict[str, str] = {}
            uploads: list[tuple[str, bytes]] = []
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition") or ""
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    uploads.append((_safe_filename(filename), payload))
                else:
                    fields[name] = payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
            tender_number = fields.get("tender_number", "").strip()
            title = fields.get("title", "").strip() or tender_number or "Новая закупка"
            requested_id = tender_number or datetime.now().strftime("tender-%Y%m%d-%H%M%S")
            case_id = slugify_case_id(requested_id)
            case_dir = project_dir / "cases" / case_id
            if not (case_dir / "case.json").exists():
                initialize_case(case_dir, case_id=case_id, title=title)
            payload = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
            payload["title"] = title
            payload["tender_number"] = tender_number
            payload["law"] = fields.get("law", "44-FZ") or "44-FZ"
            (case_dir / "case.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            documents = case_dir / "documents"
            for filename, data in uploads:
                target = _available_path(documents / filename)
                target.write_bytes(data)
            return case_id

        def _serve_case_file(self, project_dir: Path, relative: str) -> None:
            parts = [part for part in relative.split("/") if part]
            if len(parts) < 2:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            case_dir = _safe_case_dir(project_dir, parts[0])
            if case_dir is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            target = case_dir.joinpath(*parts[1:]).resolve()
            try:
                target.relative_to(case_dir.resolve())
            except ValueError:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{target.name}")
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict[str, object]:
            content_length = int(self.headers.get("Content-Length") or 0)
            if content_length <= 0 or content_length > 64 * 1024:
                raise ValueError("Недопустимый размер запроса")
            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ValueError("Некорректный JSON") from None
            if not isinstance(payload, dict):
                raise ValueError("Ожидается JSON-объект")
            return payload

        def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, value: str) -> None:
            data = value.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ControlCenterHandler


def _workflow_job(case_dir: Path) -> None:
    case_id = case_dir.name
    try:
        summary = run_case_workflow(case_dir)
        state = {"status": "completed", "message": "Анализ завершён", "summary": summary}
    except Exception as exc:  # keep the local UI alive and expose a concise recoverable error
        state = {"status": "error", "message": str(exc)}
    with JOBS_LOCK:
        JOBS[case_id] = state


def _render_dashboard(base_dir: Path, selected_id: str) -> str:
    cases = list_case_dashboards(base_dir)
    if not selected_id and cases:
        selected_id = str(cases[0]["case_id"])
    case_dir = _safe_case_dir(base_dir, selected_id) if selected_id else None
    dashboard = load_case_dashboard(case_dir) if case_dir else None
    sidebar = "".join(_render_case_link(item, selected_id) for item in cases) or '<p class="empty">Дел пока нет</p>'
    content = _render_case_content(dashboard, selected_id) if dashboard else _render_welcome()
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Тендерный агент</title><style>{_CSS}</style></head>
<body><header><div><span class="eyebrow">TLT · закупки</span><h1>Тендерный агент</h1></div><button class="secondary" onclick="openCreate()">＋ Новая закупка</button></header>
<main><aside><div class="aside-title">Тендерные дела <span>{len(cases)}</span></div>{sidebar}</aside><section class="workspace">{content}</section></main>
<dialog id="createDialog"><form id="createForm"><div class="dialog-head"><div><span class="eyebrow">Новое дело</span><h2>Загрузить закупку</h2></div><button type="button" class="icon" onclick="createDialog.close()">×</button></div>
<label>Номер закупки<input name="tender_number" placeholder="0174500001126004843" required></label>
<label>Название<input name="title" placeholder="Поставка комплектующих"></label>
<label>Закон<select name="law"><option>44-FZ</option><option>223-FZ</option></select></label>
<label class="drop">Документы закупки<input type="file" name="documents" multiple required><small>Можно выбрать PDF, DOCX, XLSX, ZIP или RAR</small></label>
<button class="primary" type="submit">Создать и загрузить</button><p id="createStatus" class="status"></p></form></dialog>
<div id="toast" class="toast"></div><script>{_JS}</script></body></html>"""


def _render_case_link(item: dict[str, object], selected_id: str) -> str:
    case_id = str(item["case_id"])
    active = " active" if case_id == selected_id else ""
    workflow = item.get("workflow") or {}
    status = str(workflow.get("status") or "не запускалось") if isinstance(workflow, dict) else "не запускалось"
    return (
        f'<a class="case-link{active}" href="/?case={html.escape(case_id)}">'
        f'<strong>{html.escape(str(item.get("title") or case_id))}</strong>'
        f'<span>{html.escape(str(item.get("tender_number") or case_id))}</span>'
        f'<small>{html.escape(status)}</small></a>'
    )


def _render_case_content(data: dict[str, object], case_id: str) -> str:
    case = data["case"]
    preflight = data.get("preflight") or {}
    supplier = data.get("supplier") or []
    alternatives = data.get("alternatives") or []
    items = data.get("items") or []
    offers = data.get("offers") or []
    economics = data.get("economics") or {}
    findings = preflight.get("findings", []) if isinstance(preflight, dict) else []
    blockers = [finding for finding in findings if finding.get("severity") == "blocker"]
    risks = [finding for finding in findings if finding.get("severity") == "risk"]
    products = sum(len(result.get("products", [])) for result in supplier)
    required_alt = sum(task.get("status") == "required" for task in alternatives)
    item_cards = "".join(
        _render_item(item, supplier, alternatives, offers, case_id) for item in items
    ) or '<p class="empty">Позиции появятся после анализа документов.</p>'
    economics_html = _render_economics(economics, case, data.get("expenses") or [], case_id)
    supplier_workspace = _render_supplier_workspace(
        data.get("alternative_suppliers") or [],
        data.get("supplier_requests") or [],
        alternatives,
        case_id,
    )
    finding_html = "".join(
        f'<li class="{html.escape(str(f.get("severity", "info")))}"><strong>{html.escape(str(f.get("severity", "")).upper())}</strong>{html.escape(str(f.get("message", "")))}</li>'
        for f in findings
    ) or "<li>Критические замечания пока не сформированы.</li>"
    return f"""<div class="hero"><div><span class="eyebrow">Дело {html.escape(case_id)}</span><h2>{html.escape(str(case.get('title') or case_id))}</h2><p>{html.escape(str(case.get('customer') or 'Заказчик будет определён из документов'))}</p></div>
<button id="runButton" class="primary" onclick="runCase('{html.escape(case_id)}')">▶ Проанализировать всё</button></div>
<div id="runStatus" class="run-status"></div>
<div class="metrics"><article><span>НМЦК</span><strong>{_money(case.get('nmck'))}</strong></article><article><span>Позиций</span><strong>{len(items)}</strong></article><article><span>Кандидатов</span><strong>{products}</strong></article><article><span>Блокеров</span><strong>{len(blockers)}</strong></article><article><span>Нужны альтернативы</span><strong>{required_alt}</strong></article></div>
{economics_html}
<div class="section-head"><div><span class="eyebrow">Подбор</span><h3>Позиции и товары</h3></div><div class="file-links"><a href="/case-files/{html.escape(case_id)}/output/preflight.md" target="_blank">Анализ ТЗ</a><a href="/case-files/{html.escape(case_id)}/output/alternative_search.md" target="_blank">Альтернативы</a><a href="/case-files/{html.escape(case_id)}/output/supplier_requests.csv">Запросы КП</a></div></div>
<div class="items">{item_cards}</div>
{supplier_workspace}
<details class="findings"><summary>Риски и вопросы <span>{len(findings)}</span></summary><ul>{finding_html}</ul></details>"""


def _render_item(
    item: dict[str, object],
    supplier: list[dict[str, object]],
    alternatives: list[dict[str, object]],
    offers: list[dict[str, object]],
    case_id: str,
) -> str:
    line_id = str(item.get("line_id") or "")
    result = next((value for value in supplier if str(value.get("line_id")) == line_id), {})
    alternative = next((value for value in alternatives if str(value.get("line_id")) == line_id), {})
    products = result.get("products", []) if isinstance(result, dict) else []
    selected = next(
        (
            offer
            for offer in offers
            if str(offer.get("line_id") or "") == line_id and bool(offer.get("selected"))
        ),
        {},
    )
    product_html = "".join(
        _render_product(
            product,
            case_id=case_id,
            line_id=line_id,
            selected=str(selected.get("sku") or "") == str(product.get("sku") or ""),
        )
        for product in products[:4]
    )
    if not product_html:
        product_html = '<p class="empty compact">У основного поставщика подходящих карточек нет.</p>'
    status = str(alternative.get("status") or "required")
    status_labels = {"required": "Искать обязательно", "verify": "Нужно подтвердить", "backup": "Резервный поиск"}
    links = "".join(
        f'<a href="{html.escape(str(link.get("url") or ""))}" target="_blank">{html.escape(str(link.get("label") or "Поиск"))}</a>'
        for link in alternative.get("search_links", [])
    )
    oem = ", ".join(alternative.get("oem_parts", [])) or "не определён"
    return f"""<article class="item-card"><div class="item-head"><span class="line">{html.escape(line_id)}</span><div><h4>{html.escape(str(item.get('name') or ''))}</h4><p>{html.escape(str(item.get('quantity') or ''))} {html.escape(str(item.get('unit') or ''))} · OEM: {html.escape(oem)}</p></div><span class="pill {html.escape(status)}">{html.escape(status_labels.get(status, status))}</span></div>
<p class="specs">{html.escape(str(item.get('required_specs') or 'Характеристики не заполнены'))}</p><div class="products">{product_html}</div><div class="alt-links">{links}</div></article>"""


def _render_product(product: dict[str, object], *, case_id: str, line_id: str, selected: bool) -> str:
    status = str(product.get("compliance_status") or "conditional")
    stock = str(product.get("stock_status") or "")
    price = _money(product.get("purchase_price_gross"))
    status_labels = {
        "exact": "Точное",
        "compliant": "Соответствует",
        "conditional": "Нужна проверка",
        "not_compliant": "Не подходит",
    }
    sku = str(product.get("sku") or "")
    if selected:
        action = (
            f'<button class="offer-button selected" data-case="{html.escape(case_id, quote=True)}" '
            f'data-line="{html.escape(line_id, quote=True)}" onclick="clearOffer(this)">✓ Выбрано</button>'
        )
    elif status == "not_compliant" or product.get("purchase_price_gross") is None:
        action = '<span class="offer-disabled">Выбор недоступен</span>'
    else:
        action = (
            f'<button class="offer-button" data-case="{html.escape(case_id, quote=True)}" '
            f'data-line="{html.escape(line_id, quote=True)}" data-sku="{html.escape(sku, quote=True)}" '
            f'data-status="{html.escape(status, quote=True)}" onclick="selectOffer(this)">Выбрать</button>'
        )
    product_url = str(product.get("product_url") or "")
    name = html.escape(str(product.get("name") or ""))
    title = f'<a href="{html.escape(product_url, quote=True)}" target="_blank">{name}</a>' if product_url else name
    return f"""<div class="product {html.escape(status)}"><div class="product-main"><strong>{title}</strong><small>SKU {html.escape(sku)} · {html.escape(stock or 'остаток неизвестен')} · {html.escape(status_labels.get(status, status))}</small></div><div class="product-action"><b>{price}</b>{action}</div></div>"""


def _render_economics(value: object, case: object, expenses: object, case_id: str) -> str:
    economics = value if isinstance(value, dict) else {}
    case_values = case if isinstance(case, dict) else {}
    expense_rows = expenses if isinstance(expenses, list) else []
    selected_count = int(economics.get("selected_count") or 0)
    total_lines = int(economics.get("total_lines") or 0)
    decision = str(economics.get("decision") or "blocked")
    labels = {
        "ready": "Можно участвовать",
        "manual_review": "Нужно решение",
        "stop": "Стоп",
        "blocked": "Нет всех данных",
    }
    expense_values = {
        str(row.get("category") or ""): str(row.get("amount_gross") or "0")
        for row in expense_rows
        if isinstance(row, dict)
    }
    report_link = (
        f'<a class="report-link" href="/case-files/{html.escape(case_id)}/output/case_report.xlsx">⬇ Excel-расчёт</a>'
        if selected_count
        else ""
    )
    procurement = _money(economics.get("procurement_gross")) if selected_count else "—"
    target_price = _money(economics.get("target_price")) if selected_count else "—"
    viable_price = _money(economics.get("viable_price")) if selected_count else "—"
    hard_floor_price = _money(economics.get("hard_floor_price")) if selected_count else "—"
    target_discount = _percent(economics.get("target_discount_from_nmck")) if selected_count else "—"
    viable_discount = _percent(economics.get("viable_discount_from_nmck")) if selected_count else "—"
    hard_floor_discount = _percent(economics.get("hard_floor_discount_from_nmck")) if selected_count else "—"
    return f"""<section class="economics {html.escape(decision)}"><div class="economics-head"><div><span class="eyebrow">Экономика по выбранным товарам</span><h3>{html.escape(labels.get(decision, decision))}</h3><p>{html.escape(str(economics.get('decision_reason') or ''))}</p></div><div><strong class="selected-counter">{selected_count} / {total_lines}</strong>{report_link}</div></div>
<div class="economics-grid"><div><span>Закупка товара</span><b>{procurement}</b></div><div><span>Цель +30%</span><b>{target_price}</b><small>снижение от НМЦК {target_discount}</small></div><div><span>Рабочий порог +15%</span><b>{viable_price}</b><small>снижение {viable_discount}</small></div><div><span>Жёсткий стоп +12%</span><b>{hard_floor_price}</b><small>снижение {hard_floor_discount}</small></div></div>
<details class="finance-edit"><summary>Изменить НМЦК, ставку и расходы</summary><form onsubmit="saveEconomics(event,this)" data-case="{html.escape(case_id, quote=True)}"><div class="finance-fields"><label>НМЦК<input name="nmck" inputmode="decimal" value="{html.escape(str(case_values.get('nmck') or ''), quote=True)}"></label><label>Плановая ставка<input name="planned_bid" inputmode="decimal" value="{html.escape(str(case_values.get('planned_bid') or ''), quote=True)}"></label><label>Доставка<input name="delivery_cost" inputmode="decimal" value="{html.escape(expense_values.get('delivery', '0'), quote=True)}"></label><label>Разгрузка<input name="unloading_cost" inputmode="decimal" value="{html.escape(expense_values.get('unloading', '0'), quote=True)}"></label><label>Монтаж<input name="installation_cost" inputmode="decimal" value="{html.escape(expense_values.get('installation', '0'), quote=True)}"></label><label>Регион<input name="region" value="{html.escape(str(case_values.get('region') or ''), quote=True)}"></label></div><button class="primary" type="submit">Пересчитать</button><span class="finance-status"></span></form></details></section>"""


def _render_supplier_workspace(
    suppliers: object,
    requests: object,
    alternatives: object,
    case_id: str,
) -> str:
    supplier_rows = suppliers if isinstance(suppliers, list) else []
    request_rows = requests if isinstance(requests, list) else []
    task_rows = alternatives if isinstance(alternatives, list) else []
    options = "".join(
        f'<option value="{html.escape(str(row.get("supplier_id") or ""), quote=True)}">'
        f'{html.escape(str(row.get("name") or ""))}</option>'
        for row in supplier_rows
        if isinstance(row, dict)
    )
    task_forms = []
    for task in task_rows:
        if not isinstance(task, dict) or task.get("status") not in {"required", "verify"}:
            continue
        line_id = str(task.get("line_id") or "")
        if options:
            action = (
                f'<form class="assign-form" data-case="{html.escape(case_id, quote=True)}" '
                f'data-line="{html.escape(line_id, quote=True)}" onsubmit="assignSupplier(event,this)">'
                f'<select name="supplier_id" required><option value="">Выберите поставщика</option>{options}</select>'
                '<button type="submit">Подготовить запрос</button></form>'
            )
        else:
            action = '<small>Сначала добавьте поставщика в реестр.</small>'
        task_forms.append(
            f'<div class="rfq-task"><div><b>{html.escape(line_id)}. {html.escape(str(task.get("item_name") or ""))}</b>'
            f'<small>OEM: {html.escape(", ".join(task.get("oem_parts", [])) or "не определён")}</small></div>{action}</div>'
        )
    active_requests = []
    for row in request_rows:
        if not isinstance(row, dict) or not str(row.get("supplier") or "").strip():
            continue
        status = str(row.get("response_status") or "подготовлен")
        status_options = "".join(
            f'<option{(" selected" if candidate == status else "")}>{candidate}</option>'
            for candidate in ("подготовлен", "отправлен", "ответ получен", "отказ", "нет ответа")
        )
        active_requests.append(
            f'<form class="request-row" data-case="{html.escape(case_id, quote=True)}" '
            f'data-line="{html.escape(str(row.get("line_id") or ""), quote=True)}" '
            f'data-supplier="{html.escape(str(row.get("supplier") or ""), quote=True)}" onsubmit="updateRequest(event,this)">'
            f'<b>{html.escape(str(row.get("line_id") or ""))}. {html.escape(str(row.get("supplier") or ""))}</b>'
            f'<a href="mailto:{html.escape(str(row.get("email") or ""), quote=True)}">{html.escape(str(row.get("email") or "нет email"))}</a>'
            f'<select name="response_status">{status_options}</select>'
            f'<input name="response_price" placeholder="Цена" value="{html.escape(str(row.get("response_price") or ""), quote=True)}">'
            f'<input name="response_stock" placeholder="Наличие" value="{html.escape(str(row.get("response_stock") or ""), quote=True)}">'
            '<button type="submit">Сохранить</button></form>'
        )
    supplier_chips = "".join(
        f'<span>{html.escape(str(row.get("name") or ""))}<small>{html.escape(str(row.get("categories") or row.get("email") or ""))}</small></span>'
        for row in supplier_rows
        if isinstance(row, dict)
    ) or '<p class="empty compact">Реестр пока пуст.</p>'
    return f"""<section class="supplier-workspace"><div class="section-head"><div><span class="eyebrow">Альтернативные поставщики</span><h3>Реестр и запросы КП</h3></div></div><div class="supplier-chips">{supplier_chips}</div>
<details class="supplier-add"><summary>＋ Добавить поставщика</summary><form onsubmit="addSupplier(event,this)"><div class="supplier-fields"><input name="name" placeholder="Название" required><input name="email" type="email" placeholder="Email"><input name="phone" placeholder="Телефон"><input name="website" placeholder="Сайт"><input name="categories" placeholder="Категории: оргтехника, климат"><button type="submit">Добавить</button></div><span class="supplier-status"></span></form></details>
<div class="rfq-tasks">{''.join(task_forms) or '<p class="empty compact">Обязательных запросов КП пока нет.</p>'}</div><div class="request-list">{''.join(active_requests)}</div></section>"""


def _render_welcome() -> str:
    return """<div class="welcome"><span class="eyebrow">Первый запуск</span><h2>Одна точка для всей закупки</h2><p>Создайте дело, загрузите документы и запустите единый анализ. Агент сам распакует архивы, выделит позиции, проверит основной каталог и подготовит альтернативный поиск.</p><button class="primary" onclick="openCreate()">＋ Загрузить первую закупку</button></div>"""


def _safe_case_dir(base_dir: Path, case_id: str) -> Path | None:
    if not case_id:
        return None
    target = (base_dir / "cases" / slugify_case_id(case_id)).resolve()
    try:
        target.relative_to((base_dir / "cases").resolve())
    except ValueError:
        return None
    return target if (target / "case.json").exists() else None


def _safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name.strip().replace("\x00", "")
    if not name or name in {".", ".."}:
        raise ValueError("Некорректное имя файла")
    return name[:180]


def _available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Слишком много файлов с именем {path.name}")


def _money(value: object) -> str:
    if value in {None, ""}:
        return "—"
    try:
        return f"{float(value):,.2f} ₽".replace(",", " ")
    except (TypeError, ValueError):
        return html.escape(str(value))


def _percent(value: object) -> str:
    if value in {None, ""}:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return html.escape(str(value))


_CSS = r"""
:root{--ink:#17232c;--muted:#68757f;--paper:#f4f1ea;--card:#fffdfa;--line:#ded9cf;--navy:#17384d;--teal:#168477;--amber:#e3a229;--red:#bd4f47;--shadow:0 14px 35px rgba(31,43,51,.08)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 "Segoe UI",Arial,sans-serif}header{height:84px;padding:0 34px;display:flex;align-items:center;justify-content:space-between;background:var(--navy);color:white;border-bottom:4px solid var(--amber)}h1,h2,h3,h4,p{margin:0}h1{font:600 25px/1.1 Georgia,serif}h2{font:600 34px/1.15 Georgia,serif;margin-top:5px}h3{font:600 25px Georgia,serif}h4{font-size:17px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:800;color:var(--teal)}header .eyebrow{color:#9bd8d0}button{font:inherit;cursor:pointer;border:0}.primary,.secondary{padding:12px 18px;border-radius:9px;font-weight:700}.primary{background:var(--amber);color:#20252a;box-shadow:0 6px 16px rgba(227,162,41,.22)}.secondary{background:rgba(255,255,255,.1);color:white;border:1px solid rgba(255,255,255,.25)}main{min-height:calc(100vh - 84px);display:grid;grid-template-columns:280px 1fr}aside{padding:24px 18px;border-right:1px solid var(--line);background:#ebe7de}.aside-title{display:flex;justify-content:space-between;padding:0 8px 15px;font-weight:800}.aside-title span,.findings summary span{background:#d9d4c9;border-radius:20px;padding:1px 8px}.case-link{display:block;padding:13px 14px;margin-bottom:8px;text-decoration:none;color:var(--ink);border-radius:10px;border:1px solid transparent}.case-link:hover,.case-link.active{background:var(--card);border-color:var(--line);box-shadow:0 7px 18px rgba(31,43,51,.05)}.case-link strong,.case-link span,.case-link small{display:block}.case-link span,.case-link small{color:var(--muted);font-size:12px;margin-top:3px}.workspace{padding:34px;max-width:1450px;width:100%;margin:0 auto}.hero{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}.hero p{color:var(--muted);margin-top:8px}.run-status{min-height:22px;margin-top:12px;color:var(--teal);font-weight:700}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0 32px}.metrics article{background:var(--card);border:1px solid var(--line);padding:16px;border-radius:12px;box-shadow:var(--shadow)}.metrics span,.metrics strong{display:block}.metrics span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}.metrics strong{font-size:24px;margin-top:7px}.section-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:14px}.file-links,.alt-links{display:flex;gap:8px;flex-wrap:wrap}.file-links a,.alt-links a{color:var(--navy);text-decoration:none;background:#e5ecee;border-radius:6px;padding:6px 9px;font-size:12px;font-weight:700}.items{display:grid;gap:14px}.item-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow)}.item-head{display:grid;grid-template-columns:38px 1fr auto;gap:12px;align-items:start}.line{width:34px;height:34px;border-radius:9px;background:var(--navy);color:white;display:grid;place-items:center;font-weight:800}.item-head p,.specs{color:var(--muted);font-size:13px}.pill{padding:5px 9px;border-radius:20px;font-size:11px;font-weight:800}.pill.required{background:#f8d9d5;color:#873a35}.pill.verify{background:#f9e8bd;color:#755211}.pill.backup{background:#d6eee9;color:#21675e}.specs{padding:12px 0;border-bottom:1px solid #ece8df}.products{display:grid;gap:7px;margin:12px 0}.product{display:flex;justify-content:space-between;gap:18px;padding:10px 12px;background:#f5f3ed;border-left:4px solid #97a2a8;border-radius:7px}.product.compliant,.product.exact{border-color:var(--teal)}.product.not_compliant{border-color:var(--red);opacity:.75}.product strong,.product small{display:block}.product small{color:var(--muted);margin-top:3px}.product b{white-space:nowrap}.findings{margin-top:20px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px}.findings summary{cursor:pointer;font-weight:800}.findings li{margin:9px 0}.findings li strong{font-size:10px;margin-right:8px;color:var(--red)}.empty{color:var(--muted);padding:15px}.empty.compact{padding:4px}.welcome{max-width:720px;margin:80px auto;background:var(--card);padding:44px;border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.welcome p{font-size:17px;color:var(--muted);margin:15px 0 24px}dialog{border:0;border-radius:16px;padding:0;max-width:620px;width:calc(100% - 30px);box-shadow:0 30px 90px rgba(0,0,0,.28)}dialog::backdrop{background:rgba(15,28,37,.58)}dialog form{padding:26px}.dialog-head{display:flex;justify-content:space-between;margin-bottom:18px}.icon{font-size:30px;background:none}label{display:block;font-weight:700;margin:13px 0}input,select{display:block;width:100%;margin-top:6px;padding:12px;border:1px solid var(--line);border-radius:8px;font:inherit;background:white}.drop{padding:15px;background:#f4f1ea;border:1px dashed #a9a398;border-radius:10px}.drop small{display:block;color:var(--muted);margin-top:6px}.status{min-height:20px;margin-top:10px}.toast{display:none;position:fixed;right:24px;bottom:24px;background:var(--navy);color:white;padding:13px 18px;border-radius:9px;box-shadow:var(--shadow)}@media(max-width:980px){main{grid-template-columns:1fr}aside{display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line)}.aside-title{display:none}.case-link{min-width:220px}.metrics{grid-template-columns:repeat(2,1fr)}.workspace{padding:22px}.hero,.section-head{align-items:flex-start;flex-direction:column}.item-head{grid-template-columns:38px 1fr}.pill{grid-column:2}}@media(max-width:560px){header{padding:0 18px}.metrics{grid-template-columns:1fr}.workspace{padding:16px}h2{font-size:28px}.file-links{display:none}}
.product strong a{color:var(--ink);text-decoration:none}.product strong a:hover{text-decoration:underline}.product-main{flex:1}.product-action{display:flex;align-items:center;gap:12px}.offer-button{padding:7px 11px;border-radius:7px;background:var(--navy);color:white;font-size:12px;font-weight:800}.offer-button.selected{background:var(--teal)}.offer-disabled{font-size:11px;color:var(--muted)}.economics{background:var(--card);border:1px solid var(--line);border-top:5px solid var(--amber);border-radius:14px;padding:20px;margin:0 0 32px;box-shadow:var(--shadow)}.economics.ready{border-top-color:var(--teal)}.economics.stop,.economics.blocked{border-top-color:var(--red)}.economics-head{display:flex;justify-content:space-between;gap:20px}.economics-head p{color:var(--muted);margin-top:6px}.selected-counter{font-size:24px;white-space:nowrap}.economics-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:17px}.economics-grid div{padding:12px;background:#f5f3ed;border-radius:8px}.economics-grid span,.economics-grid b,.economics-grid small{display:block}.economics-grid span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}.economics-grid b{font-size:19px;margin:5px 0}.economics-grid small{color:var(--muted)}@media(max-width:980px){.economics-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.economics-grid{grid-template-columns:1fr}.product{flex-direction:column}.product-action{justify-content:space-between}}
.report-link{display:block;margin-top:5px;color:var(--navy);font-size:12px;text-decoration:none;font-weight:800}.finance-edit{border-top:1px solid var(--line);margin-top:16px;padding-top:13px}.finance-edit summary{cursor:pointer;font-weight:800}.finance-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.finance-edit label{font-size:12px;margin:10px 0}.finance-edit input{padding:9px}.finance-status{margin-left:10px;color:var(--teal);font-weight:700}@media(max-width:750px){.finance-fields{grid-template-columns:1fr}}
.supplier-workspace{margin-top:32px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:var(--shadow)}.supplier-chips{display:flex;gap:8px;flex-wrap:wrap}.supplier-chips>span{background:#e5ecee;border-radius:8px;padding:7px 10px;font-weight:800}.supplier-chips small{display:block;color:var(--muted);font-weight:400}.supplier-add{margin:14px 0;border-bottom:1px solid var(--line);padding-bottom:14px}.supplier-add summary{cursor:pointer;font-weight:800}.supplier-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}.supplier-fields input{margin:0;padding:9px}.supplier-fields button,.assign-form button,.request-row button{background:var(--navy);color:white;border-radius:7px;padding:8px 10px;font-weight:800}.supplier-status{color:var(--teal)}.rfq-tasks,.request-list{display:grid;gap:8px}.rfq-task{display:flex;justify-content:space-between;gap:15px;align-items:center;padding:10px;background:#f5f3ed;border-radius:8px}.rfq-task small{display:block;color:var(--muted)}.assign-form{display:flex;gap:7px}.assign-form select{margin:0;padding:8px;min-width:210px}.request-list{margin-top:14px}.request-row{display:grid;grid-template-columns:1.2fr 1fr 1fr .65fr .8fr auto;gap:7px;align-items:center;padding:9px;border:1px solid var(--line);border-radius:8px}.request-row a{color:var(--navy);font-size:12px}.request-row input,.request-row select{margin:0;padding:7px;font-size:12px}@media(max-width:900px){.supplier-fields{grid-template-columns:1fr 1fr}.rfq-task,.assign-form{align-items:stretch;flex-direction:column}.request-row{grid-template-columns:1fr 1fr}}@media(max-width:560px){.supplier-fields,.request-row{grid-template-columns:1fr}}
main{grid-template-columns:280px minmax(0,1fr)}.workspace{min-width:0}.metrics{grid-template-columns:repeat(5,minmax(0,1fr))}.metrics article{min-width:0}.metrics strong{overflow-wrap:anywhere}@media(max-width:1200px){.metrics{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:980px){main{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.metrics{grid-template-columns:1fr}}
.items{grid-template-columns:minmax(0,1fr)}.item-card,.product-main{min-width:0}.product-main strong,.product-main a{overflow-wrap:anywhere}.product-action{flex-shrink:0}
"""


_JS = r"""
const createDialog=document.getElementById('createDialog');function openCreate(){createDialog.showModal()}function toast(message){const el=document.getElementById('toast');el.textContent=message;el.style.display='block';setTimeout(()=>el.style.display='none',3500)}
document.getElementById('createForm').addEventListener('submit',async(event)=>{event.preventDefault();const status=document.getElementById('createStatus');status.textContent='Загружаю документы…';try{const response=await fetch('/api/cases',{method:'POST',body:new FormData(event.target)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Ошибка загрузки');location.href=data.url}catch(error){status.textContent=error.message}});
async function runCase(caseId){const button=document.getElementById('runButton');const status=document.getElementById('runStatus');button.disabled=true;button.textContent='Анализ выполняется…';status.textContent='Разбираю документы, определяю OEM и проверяю поставщиков.';try{const response=await fetch(`/api/cases/${encodeURIComponent(caseId)}/run`,{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось запустить');pollJob(caseId)}catch(error){status.textContent=error.message;button.disabled=false;button.textContent='▶ Проанализировать всё'}}
async function pollJob(caseId){const status=document.getElementById('runStatus');const response=await fetch(`/api/jobs/${encodeURIComponent(caseId)}`);const data=await response.json();status.textContent=data.message||'Анализ выполняется…';if(data.status==='completed'){toast('Анализ завершён');setTimeout(()=>location.reload(),700);return}if(data.status==='error'){document.getElementById('runButton').disabled=false;return}setTimeout(()=>pollJob(caseId),1800)}
async function selectOffer(button){if(button.dataset.status==='conditional'&&!confirm('У товара есть неподтверждённые пункты ТЗ. Выбрать его для ручной проверки?'))return;button.disabled=true;button.textContent='Сохраняю…';try{const response=await fetch(`/api/cases/${encodeURIComponent(button.dataset.case)}/offers/select`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({line_id:button.dataset.line,sku:button.dataset.sku})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось выбрать товар');toast('Товар выбран, экономика пересчитана');setTimeout(()=>location.reload(),450)}catch(error){toast(error.message);button.disabled=false;button.textContent='Выбрать'}}
async function clearOffer(button){button.disabled=true;try{const response=await fetch(`/api/cases/${encodeURIComponent(button.dataset.case)}/offers/clear`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({line_id:button.dataset.line})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось снять выбор');toast('Выбор снят');setTimeout(()=>location.reload(),350)}catch(error){toast(error.message);button.disabled=false}}
async function saveEconomics(event,form){event.preventDefault();const button=form.querySelector('button');const status=form.querySelector('.finance-status');button.disabled=true;status.textContent='Считаю…';const values=Object.fromEntries(new FormData(form).entries());try{const response=await fetch(`/api/cases/${encodeURIComponent(form.dataset.case)}/economics`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось пересчитать');status.textContent=data.report_error||'Расчёт обновлён';setTimeout(()=>location.reload(),550)}catch(error){status.textContent=error.message;button.disabled=false}}
async function addSupplier(event,form){event.preventDefault();const status=form.querySelector('.supplier-status');const values=Object.fromEntries(new FormData(form).entries());try{const response=await fetch('/api/suppliers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось добавить');status.textContent='Поставщик добавлен';setTimeout(()=>location.reload(),350)}catch(error){status.textContent=error.message}}
async function assignSupplier(event,form){event.preventDefault();const values=Object.fromEntries(new FormData(form).entries());values.line_id=form.dataset.line;try{const response=await fetch(`/api/cases/${encodeURIComponent(form.dataset.case)}/requests/assign`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось подготовить запрос');toast('Запрос КП подготовлен');setTimeout(()=>location.reload(),350)}catch(error){toast(error.message)}}
async function updateRequest(event,form){event.preventDefault();const values=Object.fromEntries(new FormData(form).entries());values.line_id=form.dataset.line;values.supplier=form.dataset.supplier;try{const response=await fetch(`/api/cases/${encodeURIComponent(form.dataset.case)}/requests/update`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось сохранить');toast('Статус запроса обновлён')}catch(error){toast(error.message)}}
"""
