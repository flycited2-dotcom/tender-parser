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

from tender_parser.case_workflow import list_case_dashboards, load_case_dashboard, run_case_workflow
from tender_parser.tender_case import initialize_case, slugify_case_id


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
    findings = preflight.get("findings", []) if isinstance(preflight, dict) else []
    blockers = [finding for finding in findings if finding.get("severity") == "blocker"]
    risks = [finding for finding in findings if finding.get("severity") == "risk"]
    products = sum(len(result.get("products", [])) for result in supplier)
    required_alt = sum(task.get("status") == "required" for task in alternatives)
    item_cards = "".join(_render_item(item, supplier, alternatives) for item in items) or '<p class="empty">Позиции появятся после анализа документов.</p>'
    finding_html = "".join(
        f'<li class="{html.escape(str(f.get("severity", "info")))}"><strong>{html.escape(str(f.get("severity", "")).upper())}</strong>{html.escape(str(f.get("message", "")))}</li>'
        for f in findings
    ) or "<li>Критические замечания пока не сформированы.</li>"
    return f"""<div class="hero"><div><span class="eyebrow">Дело {html.escape(case_id)}</span><h2>{html.escape(str(case.get('title') or case_id))}</h2><p>{html.escape(str(case.get('customer') or 'Заказчик будет определён из документов'))}</p></div>
<button id="runButton" class="primary" onclick="runCase('{html.escape(case_id)}')">▶ Проанализировать всё</button></div>
<div id="runStatus" class="run-status"></div>
<div class="metrics"><article><span>НМЦК</span><strong>{_money(case.get('nmck'))}</strong></article><article><span>Позиций</span><strong>{len(items)}</strong></article><article><span>Кандидатов</span><strong>{products}</strong></article><article><span>Блокеров</span><strong>{len(blockers)}</strong></article><article><span>Нужны альтернативы</span><strong>{required_alt}</strong></article></div>
<div class="section-head"><div><span class="eyebrow">Подбор</span><h3>Позиции и товары</h3></div><div class="file-links"><a href="/case-files/{html.escape(case_id)}/output/preflight.md" target="_blank">Анализ ТЗ</a><a href="/case-files/{html.escape(case_id)}/output/alternative_search.md" target="_blank">Альтернативы</a><a href="/case-files/{html.escape(case_id)}/output/supplier_requests.csv">Запросы КП</a></div></div>
<div class="items">{item_cards}</div>
<details class="findings"><summary>Риски и вопросы <span>{len(findings)}</span></summary><ul>{finding_html}</ul></details>"""


def _render_item(item: dict[str, object], supplier: list[dict[str, object]], alternatives: list[dict[str, object]]) -> str:
    line_id = str(item.get("line_id") or "")
    result = next((value for value in supplier if str(value.get("line_id")) == line_id), {})
    alternative = next((value for value in alternatives if str(value.get("line_id")) == line_id), {})
    products = result.get("products", []) if isinstance(result, dict) else []
    product_html = "".join(_render_product(product) for product in products[:4])
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


def _render_product(product: dict[str, object]) -> str:
    status = str(product.get("compliance_status") or "conditional")
    stock = str(product.get("stock_status") or "")
    price = _money(product.get("purchase_price_gross"))
    return f"""<div class="product {html.escape(status)}"><div><strong>{html.escape(str(product.get('name') or ''))}</strong><small>SKU {html.escape(str(product.get('sku') or ''))} · {html.escape(stock or 'остаток неизвестен')}</small></div><b>{price}</b></div>"""


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


_CSS = r"""
:root{--ink:#17232c;--muted:#68757f;--paper:#f4f1ea;--card:#fffdfa;--line:#ded9cf;--navy:#17384d;--teal:#168477;--amber:#e3a229;--red:#bd4f47;--shadow:0 14px 35px rgba(31,43,51,.08)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 "Segoe UI",Arial,sans-serif}header{height:84px;padding:0 34px;display:flex;align-items:center;justify-content:space-between;background:var(--navy);color:white;border-bottom:4px solid var(--amber)}h1,h2,h3,h4,p{margin:0}h1{font:600 25px/1.1 Georgia,serif}h2{font:600 34px/1.15 Georgia,serif;margin-top:5px}h3{font:600 25px Georgia,serif}h4{font-size:17px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:800;color:var(--teal)}header .eyebrow{color:#9bd8d0}button{font:inherit;cursor:pointer;border:0}.primary,.secondary{padding:12px 18px;border-radius:9px;font-weight:700}.primary{background:var(--amber);color:#20252a;box-shadow:0 6px 16px rgba(227,162,41,.22)}.secondary{background:rgba(255,255,255,.1);color:white;border:1px solid rgba(255,255,255,.25)}main{min-height:calc(100vh - 84px);display:grid;grid-template-columns:280px 1fr}aside{padding:24px 18px;border-right:1px solid var(--line);background:#ebe7de}.aside-title{display:flex;justify-content:space-between;padding:0 8px 15px;font-weight:800}.aside-title span,.findings summary span{background:#d9d4c9;border-radius:20px;padding:1px 8px}.case-link{display:block;padding:13px 14px;margin-bottom:8px;text-decoration:none;color:var(--ink);border-radius:10px;border:1px solid transparent}.case-link:hover,.case-link.active{background:var(--card);border-color:var(--line);box-shadow:0 7px 18px rgba(31,43,51,.05)}.case-link strong,.case-link span,.case-link small{display:block}.case-link span,.case-link small{color:var(--muted);font-size:12px;margin-top:3px}.workspace{padding:34px;max-width:1450px;width:100%;margin:0 auto}.hero{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}.hero p{color:var(--muted);margin-top:8px}.run-status{min-height:22px;margin-top:12px;color:var(--teal);font-weight:700}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0 32px}.metrics article{background:var(--card);border:1px solid var(--line);padding:16px;border-radius:12px;box-shadow:var(--shadow)}.metrics span,.metrics strong{display:block}.metrics span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}.metrics strong{font-size:24px;margin-top:7px}.section-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:14px}.file-links,.alt-links{display:flex;gap:8px;flex-wrap:wrap}.file-links a,.alt-links a{color:var(--navy);text-decoration:none;background:#e5ecee;border-radius:6px;padding:6px 9px;font-size:12px;font-weight:700}.items{display:grid;gap:14px}.item-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow)}.item-head{display:grid;grid-template-columns:38px 1fr auto;gap:12px;align-items:start}.line{width:34px;height:34px;border-radius:9px;background:var(--navy);color:white;display:grid;place-items:center;font-weight:800}.item-head p,.specs{color:var(--muted);font-size:13px}.pill{padding:5px 9px;border-radius:20px;font-size:11px;font-weight:800}.pill.required{background:#f8d9d5;color:#873a35}.pill.verify{background:#f9e8bd;color:#755211}.pill.backup{background:#d6eee9;color:#21675e}.specs{padding:12px 0;border-bottom:1px solid #ece8df}.products{display:grid;gap:7px;margin:12px 0}.product{display:flex;justify-content:space-between;gap:18px;padding:10px 12px;background:#f5f3ed;border-left:4px solid #97a2a8;border-radius:7px}.product.compliant,.product.exact{border-color:var(--teal)}.product.not_compliant{border-color:var(--red);opacity:.75}.product strong,.product small{display:block}.product small{color:var(--muted);margin-top:3px}.product b{white-space:nowrap}.findings{margin-top:20px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px}.findings summary{cursor:pointer;font-weight:800}.findings li{margin:9px 0}.findings li strong{font-size:10px;margin-right:8px;color:var(--red)}.empty{color:var(--muted);padding:15px}.empty.compact{padding:4px}.welcome{max-width:720px;margin:80px auto;background:var(--card);padding:44px;border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.welcome p{font-size:17px;color:var(--muted);margin:15px 0 24px}dialog{border:0;border-radius:16px;padding:0;max-width:620px;width:calc(100% - 30px);box-shadow:0 30px 90px rgba(0,0,0,.28)}dialog::backdrop{background:rgba(15,28,37,.58)}dialog form{padding:26px}.dialog-head{display:flex;justify-content:space-between;margin-bottom:18px}.icon{font-size:30px;background:none}label{display:block;font-weight:700;margin:13px 0}input,select{display:block;width:100%;margin-top:6px;padding:12px;border:1px solid var(--line);border-radius:8px;font:inherit;background:white}.drop{padding:15px;background:#f4f1ea;border:1px dashed #a9a398;border-radius:10px}.drop small{display:block;color:var(--muted);margin-top:6px}.status{min-height:20px;margin-top:10px}.toast{display:none;position:fixed;right:24px;bottom:24px;background:var(--navy);color:white;padding:13px 18px;border-radius:9px;box-shadow:var(--shadow)}@media(max-width:980px){main{grid-template-columns:1fr}aside{display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line)}.aside-title{display:none}.case-link{min-width:220px}.metrics{grid-template-columns:repeat(2,1fr)}.workspace{padding:22px}.hero,.section-head{align-items:flex-start;flex-direction:column}.item-head{grid-template-columns:38px 1fr}.pill{grid-column:2}}@media(max-width:560px){header{padding:0 18px}.metrics{grid-template-columns:1fr}.workspace{padding:16px}h2{font-size:28px}.file-links{display:none}}
"""


_JS = r"""
const createDialog=document.getElementById('createDialog');function openCreate(){createDialog.showModal()}function toast(message){const el=document.getElementById('toast');el.textContent=message;el.style.display='block';setTimeout(()=>el.style.display='none',3500)}
document.getElementById('createForm').addEventListener('submit',async(event)=>{event.preventDefault();const status=document.getElementById('createStatus');status.textContent='Загружаю документы…';try{const response=await fetch('/api/cases',{method:'POST',body:new FormData(event.target)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Ошибка загрузки');location.href=data.url}catch(error){status.textContent=error.message}});
async function runCase(caseId){const button=document.getElementById('runButton');const status=document.getElementById('runStatus');button.disabled=true;button.textContent='Анализ выполняется…';status.textContent='Разбираю документы, определяю OEM и проверяю поставщиков.';try{const response=await fetch(`/api/cases/${encodeURIComponent(caseId)}/run`,{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось запустить');pollJob(caseId)}catch(error){status.textContent=error.message;button.disabled=false;button.textContent='▶ Проанализировать всё'}}
async function pollJob(caseId){const status=document.getElementById('runStatus');const response=await fetch(`/api/jobs/${encodeURIComponent(caseId)}`);const data=await response.json();status.textContent=data.message||'Анализ выполняется…';if(data.status==='completed'){toast('Анализ завершён');setTimeout(()=>location.reload(),700);return}if(data.status==='error'){document.getElementById('runButton').disabled=false;return}setTimeout(()=>pollJob(caseId),1800)}
"""
