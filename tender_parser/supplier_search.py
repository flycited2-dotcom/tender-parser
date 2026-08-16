from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from tender_parser.product_intelligence import build_search_queries, classify_item_kind, extract_device_models
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
    def __init__(self, api_url: str, api_token: str, *, timeout_seconds: float = 30) -> None:
        if not api_url.strip():
            raise ValueError("Не задан TENDER_SUPPLIER_API_URL")
        if len(api_token.strip()) < 32:
            raise ValueError("TENDER_SUPPLIER_API_TOKEN должен содержать не менее 32 символов")
        self.api_url = api_url.strip()
        self.api_token = api_token.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "TenderProductApiGateway":
        return cls(
            os.environ.get("TENDER_SUPPLIER_API_URL", ""),
            os.environ.get("TENDER_SUPPLIER_API_TOKEN", ""),
        )

    def search(self, query: str, *, limit: int = 10) -> tuple[int, list[SupplierProduct]]:
        response = requests.post(
            self.api_url,
            json={"query": query, "limit": max(1, min(int(limit), 20))},
            headers={"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError("Каталог поставщика вернул некорректный ответ")
        products = [_parse_product(value) for value in payload.get("products", []) if isinstance(value, dict)]
        return int(payload.get("total") or len(products)), products


def search_case_products(
    case_dir: Path,
    gateway: SupplierProductGateway,
    *,
    limit_per_item: int = 10,
) -> list[LineSearchResult]:
    _, items, _, _ = load_case(case_dir)
    if not items:
        raise ValueError("В items.csv нет подтвержденных позиций для поиска")
    cached_gateway = _CachingGateway(gateway)
    def search_item(item: LineItem) -> LineSearchResult:
        queries = build_search_queries(item.name, item.required_specs) or (_search_query(item),)
        attempted: list[str] = []
        collected: dict[str, SupplierProduct] = {}
        errors: list[str] = []
        for query in queries[:3]:
            attempted.append(query)
            try:
                _, products = cached_gateway.search(query, limit=limit_per_item)
            except (OSError, ValueError, requests.RequestException) as exc:
                errors.append(str(exc))
                continue
            for product in products:
                evaluated = _evaluate_product(item, product)
                collected[evaluated.sku or evaluated.name] = evaluated
            if any(product.compliance_status != "not_compliant" for product in collected.values()):
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
                        "I-T-P",
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
