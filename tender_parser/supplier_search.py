from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from tender_parser.product_intelligence import build_search_queries, classify_item_kind, extract_device_models
from tender_parser.climate_routing import is_climate_request
from tender_parser.tender_case import LineItem, load_case


@dataclass(frozen=True)
class RequirementCheck:
    requirement: str
    status: str
    product_value: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SupplierProduct:
    sku: str
    name: str
    purchase_price_gross: float | None
    stock_status: str
    is_available: bool
    delivery_days: int | None
    vendor: str = ""
    part: str = ""
    category: str = ""
    warranty: str = ""
    description: str = ""
    product_url: str = ""
    updated_at: str = ""
    attributes: tuple[dict[str, object], ...] = ()
    specifications: object = None
    source: str = ""
    supplier_name: str = ""
    compliance_status: str = "conditional"
    compliance_checks: tuple[RequirementCheck, ...] = ()


@dataclass(frozen=True)
class LineSearchResult:
    line_id: str
    item_name: str
    required_specs: str
    query: str
    total_found: int
    attempted_queries: tuple[str, ...] = ()
    products: list[SupplierProduct] = field(default_factory=list)
    error: str = ""


class SupplierProductGateway(Protocol):
    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        ...


class _CachingGateway:
    def __init__(self, gateway: SupplierProductGateway) -> None:
        self.gateway = gateway
        self.condition = threading.Condition()
        self.pending: set[tuple[str, int]] = set()
        self.cache: dict[tuple[str, int], tuple[int, list[SupplierProduct]] | Exception] = {}

    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        key = (query.casefold().strip(), limit)
        with self.condition:
            while key in self.pending:
                self.condition.wait()
            cached = self.cache.get(key)
            if cached is not None:
                if isinstance(cached, Exception):
                    raise cached
                return cached
            self.pending.add(key)
        try:
            result = self.gateway.search(query, limit=limit)
            cached_result: tuple[int, list[SupplierProduct]] | Exception = result
        except Exception as exc:
            cached_result = exc
        with self.condition:
            self.cache[key] = cached_result
            self.pending.discard(key)
            self.condition.notify_all()
        if isinstance(cached_result, Exception):
            raise cached_result
        return cached_result


class TenderProductApiGateway:
    max_limit = 20

    def __init__(
        self,
        api_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 30,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.75,
    ) -> None:
        if not api_url.strip():
            raise ValueError("Не задан TENDER_SUPPLIER_API_URL")
        if len(api_token.strip()) < 32:
            raise ValueError("TENDER_SUPPLIER_API_TOKEN должен содержать не менее 32 символов")
        self.api_url = api_url.strip()
        self.api_token = api_token.strip()
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    @classmethod
    def from_environment(cls) -> "TenderProductApiGateway":
        return cls(
            os.environ.get("TENDER_SUPPLIER_API_URL", ""),
            os.environ.get("TENDER_SUPPLIER_API_TOKEN", ""),
        )

    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        response = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = requests.post(
                    self.api_url,
                    json={"query": query, "limit": max(1, min(int(limit), self.max_limit))},
                    headers={"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt >= self.retry_attempts:
                    raise
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if attempt >= self.retry_attempts or (status != 429 and status < 500):
                    raise
            time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
        if response is None:  # pragma: no cover - defensive, the loop always runs at least once
            raise RuntimeError("Каталог поставщика не вернул ответ")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError("Каталог поставщика вернул некорректный ответ")
        products = [_parse_product(value) for value in payload.get("products", []) if isinstance(value, dict)]
        return int(payload.get("total") or len(products)), products


class ClimateProductApiGateway(TenderProductApiGateway):
    """Read-only gateway to the owner's climate supplier hub."""

    max_limit = 100

    def __init__(
        self,
        api_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 30,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.75,
    ) -> None:
        if not api_url.strip():
            raise ValueError("Не задан TENDER_CLIMATE_API_URL")
        if len(api_token.strip()) < 32:
            raise ValueError("TENDER_CLIMATE_API_TOKEN должен содержать не менее 32 символов")
        super().__init__(
            api_url,
            api_token,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    @classmethod
    def from_environment(cls) -> "ClimateProductApiGateway":
        return cls(
            os.environ.get("TENDER_CLIMATE_API_URL", ""),
            os.environ.get("TENDER_CLIMATE_API_TOKEN", ""),
        )


class ClimateProductSshGateway:
    """Read-only one-shot access to Content Factory through encrypted SSH."""

    def __init__(
        self,
        ssh_host: str,
        identity_file: str,
        *,
        ssh_port: int = 22,
        ssh_bind_address: str = "",
        remote_dir: str = "/opt/content-factory",
        timeout_seconds: float = 180,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+", ssh_host.strip()):
            raise ValueError("TENDER_CLIMATE_SSH_HOST должен иметь вид user@host")
        key = Path(identity_file).expanduser()
        if not key.is_file():
            raise ValueError("Файл TENDER_CLIMATE_SSH_KEY не найден")
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", remote_dir.strip()):
            raise ValueError("TENDER_CLIMATE_REMOTE_DIR содержит недопустимые символы")
        self.ssh_host = ssh_host.strip()
        if not 1 <= int(ssh_port) <= 65535:
            raise ValueError("TENDER_CLIMATE_SSH_PORT должен быть от 1 до 65535")
        self.ssh_port = int(ssh_port)
        self.ssh_bind_address = ssh_bind_address.strip()
        self.identity_file = str(key.resolve())
        self.remote_dir = remote_dir.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "ClimateProductSshGateway":
        return cls(
            os.environ.get("TENDER_CLIMATE_SSH_HOST", ""),
            os.environ.get("TENDER_CLIMATE_SSH_KEY", ""),
            ssh_port=int(os.environ.get("TENDER_CLIMATE_SSH_PORT", "22")),
            ssh_bind_address=os.environ.get("TENDER_CLIMATE_SSH_BIND_ADDRESS", ""),
            remote_dir=os.environ.get("TENDER_CLIMATE_REMOTE_DIR", "/opt/content-factory"),
        )

    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        return self._search_cli(query, limit=limit, command_name="search", catalog_label="Климатический каталог")

    def _search_cli(
        self,
        query: str,
        *,
        limit: int,
        command_name: str,
        catalog_label: str,
    ) -> tuple[int, list[SupplierProduct]]:
        normalized_query = " ".join(query.split()).strip()
        if not normalized_query:
            raise ValueError("Поисковый запрос не может быть пустым")
        encoded_query = base64.urlsafe_b64encode(normalized_query.encode("utf-8")).decode("ascii")
        safe_limit = max(1, min(int(limit), 100))
        remote_command = (
            f"cd {self.remote_dir} && PYTHONPATH=src .venv/bin/python "
            f"-m content_factory.tender_catalog_cli {command_name} "
            f"--query-base64 {encoded_query} --limit {safe_limit}"
        )
        command = [
            "ssh", "-T", "-i", self.identity_file,
            "-p", str(self.ssh_port),
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=12",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "LogLevel=ERROR",
        ]
        if self.ssh_bind_address:
            command.extend(["-b", self.ssh_bind_address])
        command.extend([self.ssh_host, remote_command])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "SSH command failed").strip()[-1000:]
            raise OSError(f"{catalog_label} по SSH недоступен: {detail}")
        payload = _last_json_object(completed.stdout)
        if payload.get("ok") is not True:
            raise ValueError(f"{catalog_label} вернул некорректный ответ")
        products = [_parse_product(value) for value in payload.get("products", []) if isinstance(value, dict)]
        return int(payload.get("total") or len(products)), products


class PrivatePriceSshGateway(ClimateProductSshGateway):
    """Read-only universal search over price files synchronized in Content Factory."""

    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        return self._search_cli(
            query,
            limit=limit,
            command_name="search-prices",
            catalog_label="Приватные прайсы поставщиков",
        )


def climate_gateway_from_environment() -> SupplierProductGateway:
    if os.environ.get("TENDER_CLIMATE_SSH_HOST", "").strip():
        return ClimateProductSshGateway.from_environment()
    return ClimateProductApiGateway.from_environment()


def private_price_gateway_from_environment() -> SupplierProductGateway:
    """Use the existing encrypted Content Factory SSH bridge for general prices."""
    ssh_host = os.environ.get("TENDER_CATALOG_SSH_HOST", "").strip() or os.environ.get(
        "TENDER_CLIMATE_SSH_HOST", ""
    ).strip()
    key = os.environ.get("TENDER_CATALOG_SSH_KEY", "").strip() or os.environ.get(
        "TENDER_CLIMATE_SSH_KEY", ""
    ).strip()
    port = os.environ.get("TENDER_CATALOG_SSH_PORT", "").strip() or os.environ.get(
        "TENDER_CLIMATE_SSH_PORT", "22"
    ).strip()
    bind = os.environ.get("TENDER_CATALOG_SSH_BIND_ADDRESS", "").strip() or os.environ.get(
        "TENDER_CLIMATE_SSH_BIND_ADDRESS", ""
    ).strip()
    remote_dir = os.environ.get("TENDER_CATALOG_REMOTE_DIR", "").strip() or os.environ.get(
        "TENDER_CLIMATE_REMOTE_DIR", "/opt/content-factory"
    ).strip()
    return PrivatePriceSshGateway(
        ssh_host,
        key,
        ssh_port=int(port),
        ssh_bind_address=bind,
        remote_dir=remote_dir,
    )


def _last_json_object(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Климатический каталог не вернул JSON")


def search_case_products(
    case_dir: Path,
    gateway: SupplierProductGateway,
    *,
    limit_per_item: int = 10,
    climate_gateway: SupplierProductGateway | None = None,
    private_price_gateway: SupplierProductGateway | None = None,
) -> list[LineSearchResult]:
    _, items, _, _ = load_case(case_dir)
    if not items:
        raise ValueError("В items.csv нет подтвержденных позиций для поиска")
    cached_gateway = _CachingGateway(gateway)
    cached_climate_gateway = _CachingGateway(climate_gateway) if climate_gateway is not None else None
    cached_private_price_gateway = (
        _CachingGateway(private_price_gateway) if private_price_gateway is not None else None
    )
    def search_item(item: LineItem) -> LineSearchResult:
        queries = build_search_queries(item.name, item.required_specs) or (_search_query(item),)
        attempted: list[str] = []
        collected: dict[str, SupplierProduct] = {}
        errors: list[str] = []
        route = [cached_gateway]
        if cached_private_price_gateway is not None:
            route.insert(0, cached_private_price_gateway)
        if cached_climate_gateway is not None and is_climate_request(item.name, item.required_specs):
            route.insert(0, cached_climate_gateway)
        for active_gateway in route:
            viable_before = sum(product.compliance_status != "not_compliant" for product in collected.values())
            for query in queries[:3]:
                attempted.append(query)
                try:
                    _, products = active_gateway.search(query, limit=limit_per_item)
                except (OSError, ValueError, requests.RequestException) as exc:
                    errors.append(str(exc))
                    continue
                for product in products:
                    evaluated = _evaluate_product(item, product)
                    identity = "|".join((evaluated.source, evaluated.sku or evaluated.name)).casefold()
                    collected[identity] = evaluated
                if any(product.compliance_status != "not_compliant" for product in collected.values()):
                    break
            viable_after = sum(product.compliance_status != "not_compliant" for product in collected.values())
            if viable_after > viable_before:
                break
        products = sorted(collected.values(), key=_product_rank)
        error = "; ".join(dict.fromkeys(errors)) if errors and not products else ""
        return LineSearchResult(
            line_id=item.line_id,
            item_name=item.name,
            required_specs=item.required_specs,
            query=attempted[0],
            total_found=len(products),
            attempted_queries=tuple(attempted),
            products=products,
            error=error,
        )

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(items)))) as pool:
        return list(pool.map(search_item, items))


def export_supplier_search(results: list[LineSearchResult], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "supplier_candidates.json"
    offers_path = output_dir / "offers_draft.csv"
    summary_path = output_dir / "supplier_search.md"
    json_path.write_text(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2), encoding="utf-8")

    headers = [
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
    with offers_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers)
        for result in results:
            for product in result.products:
                if product.purchase_price_gross is None:
                    continue
                writer.writerow(
                    [
                        result.line_id,
                        product.supplier_name or product.source or "I-T-P",
                        product.sku,
                        product.name,
                        product.purchase_price_gross,
                        product.compliance_status,
                        "no",
                        _stock_label(product),
                        product.delivery_days if product.delivery_days is not None else "",
                        "0.22",
                        product.product_url,
                        _compliance_evidence(product),
                        f"Запрос: {result.query}; бренд: {product.vendor}; артикул: {product.part}",
                    ]
                )
    summary_path.write_text(_render_summary(results), encoding="utf-8")
    return {"json": json_path, "offers": offers_path, "summary": summary_path}


def _parse_product(payload: dict[str, object]) -> SupplierProduct:
    price = payload.get("purchasePriceGross")
    return SupplierProduct(
        sku=str(payload.get("sku") or ""),
        name=str(payload.get("name") or payload.get("supplierName") or ""),
        purchase_price_gross=float(price) if price is not None else None,
        stock_status=str(payload.get("stockStatus") or ""),
        is_available=bool(payload.get("isAvailable")),
        delivery_days=_optional_int(payload.get("deliveryDays")),
        vendor=str(payload.get("vendor") or ""),
        part=str(payload.get("part") or ""),
        category=str(payload.get("category") or ""),
        warranty=str(payload.get("warranty") or ""),
        description=str(payload.get("description") or ""),
        product_url=str(payload.get("productUrl") or ""),
        updated_at=str(payload.get("updatedAt") or ""),
        attributes=tuple(value for value in payload.get("attributes", []) if isinstance(value, dict)),
        specifications=payload.get("specifications"),
        source=str(payload.get("source") or ""),
        supplier_name=str(payload.get("supplierName") or ""),
    )


def _search_query(item: LineItem) -> str:
    queries = build_search_queries(item.name, item.required_specs)
    return queries[0] if queries else " ".join(item.name.split())[:500]


def _stock_label(product: SupplierProduct) -> str:
    labels = {"plenty": "Много", "available": "В наличии", "low": "Мало", "out": "Нет в наличии"}
    return labels.get(product.stock_status, product.stock_status or ("В наличии" if product.is_available else "Неизвестно"))


def _optional_int(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value)))


def _render_summary(results: list[LineSearchResult]) -> str:
    lines = [
        "# Кандидаты товаров основного поставщика",
        "",
        "> Автосверка использует только явно доступные характеристики. Итоговое соответствие подтверждается паспортом или спецификацией производителя.",
        "",
    ]
    for result in results:
        lines.append(f"## {result.line_id}. {result.item_name}")
        lines.append("")
        lines.append(f"Запрос: `{result.query}`. Всего совпадений в каталоге: {result.total_found}.")
        if len(result.attempted_queries) > 1:
            checked = ", ".join(f"`{query}`" for query in result.attempted_queries)
            lines.append(f"Проверенные варианты: {checked}.")
        lines.append("")
        if result.error:
            lines.append(f"Ошибка поиска: {result.error}")
        elif not result.products:
            lines.append("Товар не найден. Нужен альтернативный поставщик.")
        else:
            for product in result.products:
                price = f"{product.purchase_price_gross:,.2f} ₽" if product.purchase_price_gross is not None else "цена не указана"
                lines.append(
                    f"- **{product.compliance_status}** · SKU {product.sku}: {product.name} — {price}; "
                    f"{_stock_label(product)}; срок {product.delivery_days if product.delivery_days is not None else '?'} дн."
                )
                for check in product.compliance_checks:
                    value = f" → {check.product_value}" if check.product_value else ""
                    lines.append(f"  - {check.status}: {check.requirement}{value} ({check.reason})")
        lines.append("")
    return "\n".join(lines)


def _evaluate_product(item: LineItem, product: SupplierProduct) -> SupplierProduct:
    requirements = [part.strip(" .") for part in re.split(r"[;\n]+", item.required_specs) if part.strip(" .")]
    identity_checks = _identity_checks(item, product)
    if not requirements:
        checks = identity_checks + (RequirementCheck("Характеристики ТЗ не заполнены", "unknown", reason="Нечего сравнивать"),)
        return _replace_evaluation(product, "conditional", checks)
    checks = identity_checks + tuple(_evaluate_requirement(requirement, product) for requirement in requirements)
    if any(check.status == "fail" for check in checks):
        status = "not_compliant"
    elif checks and all(check.status == "pass" for check in checks):
        status = "compliant"
    else:
        status = "conditional"
    return _replace_evaluation(product, status, checks)


def _replace_evaluation(
    product: SupplierProduct, status: str, checks: tuple[RequirementCheck, ...]
) -> SupplierProduct:
    values = {**product.__dict__, "compliance_status": status, "compliance_checks": checks}
    return SupplierProduct(**values)


def _evaluate_requirement(requirement: str, product: SupplierProduct) -> RequirementCheck:
    numeric = re.search(
        r"^(?P<label>.+?)\s*:?[ ]*(?P<operator>не\s+менее|не\s+более|более|менее|>=|<=|≥|≤|=)\s*"
        r"(?P<number>\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*(?P<unit>[^,.;]*)$",
        requirement,
        flags=re.IGNORECASE,
    )
    if numeric:
        label = numeric.group("label").strip(" :-")
        attribute = _best_attribute(label, product.attributes)
        if attribute is None:
            fallback = _product_text_number(product)
            if fallback is None:
                return RequirementCheck(requirement, "unknown", reason="Нет сопоставимой структурированной характеристики")
            actual, actual_unit, display = fallback
            expected = float(
                numeric.group("number").replace(" ", "").replace("\u00a0", "").replace(",", ".")
            )
            expected_unit = _normalize_unit(numeric.group("unit"))
            converted = _convert_unit(actual, actual_unit, expected_unit)
            if converted is None:
                return RequirementCheck(requirement, "unknown", display, "Единицы измерения нельзя надежно сопоставить")
            passed = _compare(converted, expected, numeric.group("operator"))
            return RequirementCheck(
                requirement,
                "pass" if passed else "fail",
                display,
                "Число подтверждено карточкой товара" if passed else "Число в карточке ниже требования",
            )
        actual = _attribute_number(attribute)
        if actual is None:
            return RequirementCheck(
                requirement,
                "unknown",
                str(attribute.get("value") or ""),
                "Значение характеристики не удалось привести к числу",
            )
        expected = float(numeric.group("number").replace(" ", "").replace("\u00a0", "").replace(",", "."))
        expected_unit = _normalize_unit(numeric.group("unit"))
        actual_unit = _normalize_unit(str(attribute.get("unit") or attribute.get("value") or ""))
        converted = _convert_unit(actual, actual_unit, expected_unit)
        if converted is None:
            return RequirementCheck(
                requirement,
                "unknown",
                _attribute_display(attribute),
                "Единицы измерения нельзя надежно сопоставить",
            )
        passed = _compare(converted, expected, numeric.group("operator"))
        return RequirementCheck(
            requirement,
            "pass" if passed else "fail",
            _attribute_display(attribute),
            "Числовое условие выполнено" if passed else "Числовое условие не выполнено",
        )

    exact = re.match(r"^(.{2,100}?)\s*[:=]\s*(.+)$", requirement)
    product_text = _product_text(product)
    if exact:
        label, expected = exact.groups()
        if _normalize(expected) in {"да", "есть", "наличие"} and "совместим" in _normalize(label):
            models = extract_device_models(label)
            normalized_product = _product_text(product)
            missing = [model for model in models if _normalize_model(model) not in _normalize_model(normalized_product)]
            variants = [token for token in ("азия", "европа") if token in _normalize(label)]
            if missing:
                return RequirementCheck(requirement, "fail", product.name, "Не все требуемые модели указаны в карточке")
            if models and any(token not in normalized_product for token in variants):
                return RequirementCheck(requirement, "unknown", product.name, "Региональная модификация не подтверждена")
            if models:
                return RequirementCheck(requirement, "pass", product.name, "Совместимые модели указаны в карточке")
        attribute = _best_attribute(label, product.attributes)
        if attribute is not None:
            actual = str(attribute.get("value") or "")
            passed = _normalize(expected) in _normalize(actual) or _normalize(actual) in _normalize(expected)
            return RequirementCheck(
                requirement,
                "pass" if passed else "fail",
                _attribute_display(attribute),
                "Значение совпадает" if passed else "Значение не совпадает",
            )
    if _normalize(requirement) and _normalize(requirement) in product_text:
        return RequirementCheck(requirement, "pass", requirement, "Формулировка найдена в карточке товара")
    return RequirementCheck(requirement, "unknown", reason="В карточке недостаточно данных для доказательства")


def _best_attribute(label: str, attributes: tuple[dict[str, object], ...]) -> dict[str, object] | None:
    wanted = _tokens(label)
    if not wanted:
        return None
    best: tuple[float, dict[str, object]] | None = None
    for attribute in attributes:
        candidate = _tokens(f"{attribute.get('label', '')} {attribute.get('key', '')}")
        if not candidate:
            continue
        overlap = len(wanted & candidate)
        score = overlap / max(len(wanted), 1)
        if overlap and (best is None or score > best[0]):
            best = (score, attribute)
    return best[1] if best and best[0] >= 0.5 else None


def _tokens(value: str) -> set[str]:
    stop = {"не", "менее", "более", "наличие", "значение", "показатель", "должен", "быть"}
    return {token for token in re.findall(r"[a-zа-я0-9]+", _normalize(value)) if len(token) > 1 and token not in stop}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip()


def _attribute_number(attribute: dict[str, object]) -> float | None:
    value = attribute.get("numericValue")
    if value is not None:
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(attribute.get("value") or ""))
    return float(match.group().replace(",", ".")) if match else None


def _attribute_display(attribute: dict[str, object]) -> str:
    label = str(attribute.get("label") or attribute.get("key") or "")
    value = str(attribute.get("value") or attribute.get("numericValue") or "")
    unit = str(attribute.get("unit") or "")
    return " ".join(part for part in (label + ":", value, unit) if part).strip()


def _normalize_unit(value: str) -> str:
    text = _normalize(value)
    aliases = {
        "гб": "gb",
        "gb": "gb",
        "тб": "tb",
        "tb": "tb",
        "мб": "mb",
        "mb": "mb",
        "квт": "kw",
        "kw": "kw",
        "вт": "w",
        "w": "w",
        "см": "cm",
        "мм": "mm",
        "м": "m",
        "дюйм": "inch",
        "дюймов": "inch",
        "\"": "inch",
        "лист": "pages",
        "листов": "pages",
        "стр": "pages",
        "страниц": "pages",
        "pages": "pages",
    }
    for token, normalized in aliases.items():
        if re.search(rf"(?:^|\s|\d){re.escape(token)}(?:\s|$)", text):
            return normalized
    return ""


def _convert_unit(value: float, actual_unit: str, expected_unit: str) -> float | None:
    if not expected_unit:
        return value
    if actual_unit == expected_unit:
        return value
    conversions = {
        ("tb", "gb"): 1024.0,
        ("mb", "gb"): 1 / 1024.0,
        ("kw", "w"): 1000.0,
        ("w", "kw"): 1 / 1000.0,
        ("m", "cm"): 100.0,
        ("cm", "mm"): 10.0,
        ("mm", "cm"): 0.1,
    }
    factor = conversions.get((actual_unit, expected_unit))
    return value * factor if factor is not None else None


def _compare(actual: float, expected: float, operator: str) -> bool:
    normalized = re.sub(r"\s+", " ", operator.lower()).strip()
    if normalized in {"не менее", ">=", "≥"}:
        return actual >= expected
    if normalized in {"не более", "<=", "≤"}:
        return actual <= expected
    if normalized == "более":
        return actual > expected
    if normalized == "менее":
        return actual < expected
    return actual == expected


def _product_text(product: SupplierProduct) -> str:
    attributes = " ".join(
        f"{attribute.get('label', '')} {attribute.get('value', '')}" for attribute in product.attributes
    )
    specs = json.dumps(product.specifications, ensure_ascii=False) if product.specifications is not None else ""
    return _normalize(" ".join((product.name, product.description, attributes, specs)))


def _compliance_evidence(product: SupplierProduct) -> str:
    passed = sum(check.status == "pass" for check in product.compliance_checks)
    failed = sum(check.status == "fail" for check in product.compliance_checks)
    unknown = sum(check.status == "unknown" for check in product.compliance_checks)
    return (
        f"Автосверка каталога: pass={passed}, fail={failed}, unknown={unknown}; "
        "окончательное соответствие подтверждается паспортом/спецификацией"
    )


def _identity_checks(item: LineItem, product: SupplierProduct) -> tuple[RequirementCheck, ...]:
    kind = classify_item_kind(item.name)
    text = _product_text(product)
    name = _normalize(product.name + " " + product.part)
    status = "unknown"
    reason = "Тип комплектующей требует ручной проверки"
    if kind == "drum_unit":
        if re.search(r"\bopc\b|фоточувствительн\w*\s+барабан", name):
            status, reason = "fail", "Найден отдельный OPC-барабан, а требуется блок/драм-юнит в сборе"
        elif re.search(r"\bdr[- ]?\d|драм юнит|блок фотобарабана|фотобарабан", name):
            status, reason = "pass", "Карточка относится к фотобарабану/драм-юниту"
    elif kind in {"drum_chip", "toner_chip"}:
        status = "pass" if "чип" in text else "fail"
        reason = "Найден отдельный чип" if status == "pass" else "Карточка относится не к отдельному чипу"
    elif kind == "fuser":
        if re.search(r"привод|шестерн|ролик|лампа|термоплен|подшипник", name):
            status, reason = "fail", "Найдена отдельная деталь термоблока, а требуется фьюзер в сборе"
        else:
            status = "pass" if re.search(r"термоблок|фьюзер|печка|\bfk[- ]?\d", name) else "fail"
            reason = "Карточка относится к термоблоку" if status == "pass" else "Тип товара не похож на термоблок"
    elif kind == "fuser_film":
        status = "pass" if "термоплен" in name else "fail"
        reason = "Карточка относится к термопленке" if status == "pass" else "Тип товара не похож на термопленку"
    if kind == "generic":
        return ()
    return (RequirementCheck("Тип товара", status, product.name, reason),)


def _normalize_model(value: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", _normalize(value))


def _product_text_number(product: SupplierProduct) -> tuple[float, str, str] | None:
    value = " ".join((product.name, product.description))
    matches = list(
        re.finditer(
            r"(?P<number>\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*(?P<unit>тыс\.?|k\.?|к\.?|стр\.?|страниц\w*|лист\w*)",
            value,
            flags=re.IGNORECASE,
        )
    )
    if not matches:
        return None
    match = matches[-1]
    number = float(match.group("number").replace(" ", "").replace("\u00a0", "").replace(",", "."))
    unit = _normalize(match.group("unit"))
    if unit.startswith(("тыс", "k", "к")) and number < 1000:
        number *= 1000
    return number, "pages", match.group(0)


def _product_rank(product: SupplierProduct) -> tuple[int, int, float, str]:
    compliance = {"compliant": 0, "exact": 0, "conditional": 1, "not_compliant": 2}
    stock = 0 if product.is_available or product.stock_status in {"plenty", "available", "low"} else 1
    price = product.purchase_price_gross if product.purchase_price_gross is not None else float("inf")
    return compliance.get(product.compliance_status, 1), stock, price, product.name
