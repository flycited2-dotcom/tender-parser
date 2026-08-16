from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Literal


ZERO = Decimal("0")
VAT_22 = Decimal("0.22")
MONEY_STEP = Decimal("0.01")

ComplianceStatus = Literal["exact", "compliant", "conditional", "not_compliant", "not_found"]
DecisionStatus = Literal["ready", "manual_review", "stop", "blocked"]


@dataclass(frozen=True)
class BusinessRules:
    target_markup: Decimal = Decimal("0.30")
    viable_markup: Decimal = Decimal("0.15")
    hard_floor_markup: Decimal = Decimal("0.12")
    vat_rate: Decimal = VAT_22
    standard_payment_days: int = 7
    tolerated_payment_days: int = 14
    standard_transit_days: int = 7
    standard_regions: tuple[str, ...] = (
        "республика крым",
        "крым",
        "симферополь",
        "севастополь",
    )
    individual_logistics_regions: tuple[str, ...] = (
        "запорожская область",
        "херсонская область",
    )
    excluded_topics: tuple[str, ...] = (
        "строительство дорог",
        "капитальное строительство",
        "бензин",
        "топливо",
        "гсм",
        "лекарственные препараты",
        "лекарства",
    )


@dataclass(frozen=True)
class TenderCase:
    case_id: str
    title: str
    tender_number: str = ""
    source_url: str = ""
    law: str = ""
    customer: str = ""
    region: str = ""
    delivery_address: str = ""
    nmck: Decimal | None = None
    planned_bid: Decimal | None = None
    payment_days: int = 7
    delivery_days: int | None = None
    entity: str = "auto"
    requires_installation: bool = False
    notes: str = ""


@dataclass(frozen=True)
class LineItem:
    line_id: str
    name: str
    quantity: Decimal
    unit: str = "шт."
    required_specs: str = ""
    mandatory: bool = True


@dataclass(frozen=True)
class ProductOffer:
    line_id: str
    supplier: str
    sku: str
    product_name: str
    unit_cost_gross: Decimal
    compliance_status: ComplianceStatus
    selected: bool = False
    stock: str = ""
    lead_days: int | None = None
    vat_rate: Decimal = VAT_22
    source_url: str = ""
    evidence: str = ""
    notes: str = ""


@dataclass(frozen=True)
class CaseExpense:
    category: str
    description: str
    amount_gross: Decimal
    vat_rate: Decimal = ZERO
    vat_reclaimable: bool = False
    confirmed: bool = True
    notes: str = ""


@dataclass(frozen=True)
class SelectedLine:
    item: LineItem
    offer: ProductOffer | None

    @property
    def total_cost_gross(self) -> Decimal:
        if self.offer is None:
            return ZERO
        return money(self.item.quantity * self.offer.unit_cost_gross)


@dataclass(frozen=True)
class EntityEconomics:
    entity: str
    sale_price_gross: Decimal
    revenue_net: Decimal
    procurement_cost: Decimal
    expense_cost: Decimal
    profit_before_income_tax: Decimal
    estimated_tax: Decimal | None
    minimum_tax_reference: Decimal | None
    profit_after_estimated_tax: Decimal | None
    profit_rate_on_cost: Decimal | None
    note: str


@dataclass(frozen=True)
class CaseEconomics:
    selected_lines: list[SelectedLine]
    expenses: list[CaseExpense]
    procurement_gross: Decimal
    expenses_gross: Decimal
    target_price: Decimal
    viable_price: Decimal
    hard_floor_price: Decimal
    assessment_price: Decimal | None
    headroom_to_target: Decimal | None
    target_discount_from_nmck: Decimal | None
    viable_discount_from_nmck: Decimal | None
    hard_floor_discount_from_nmck: Decimal | None
    decision: DecisionStatus
    decision_reason: str
    risks: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    entity_scenarios: list[EntityEconomics] = field(default_factory=list)


def initialize_case(case_dir: Path, *, case_id: str, title: str = "") -> list[Path]:
    if case_dir.exists() and any(case_dir.iterdir()):
        raise FileExistsError(f"Тендерное дело уже существует и не пусто: {case_dir}")
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "documents").mkdir(exist_ok=True)
    (case_dir / "supplier_responses").mkdir(exist_ok=True)
    (case_dir / "output").mkdir(exist_ok=True)

    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "title": title,
        "tender_number": "",
        "source_url": "",
        "law": "44-FZ",
        "customer": "",
        "region": "",
        "delivery_address": "",
        "nmck": None,
        "planned_bid": None,
        "payment_days": 7,
        "delivery_days": None,
        "entity": "auto",
        "requires_installation": False,
        "notes": "",
    }
    case_path = case_dir / "case.json"
    case_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    items_path = case_dir / "items.csv"
    _write_csv(
        items_path,
        ["line_id", "name", "quantity", "unit", "required_specs", "mandatory"],
        [["1", "", "1", "шт.", "", "yes"]],
    )
    offers_path = case_dir / "offers.csv"
    _write_csv(
        offers_path,
        [
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
        ],
        [],
    )
    expenses_path = case_dir / "expenses.csv"
    _write_csv(
        expenses_path,
        ["category", "description", "amount_gross", "vat_rate", "vat_reclaimable", "confirmed", "notes"],
        [
            ["delivery", "Доставка", "0", "0", "no", "no", "Заполнить перед финальным решением"],
            ["unloading", "Разгрузка", "0", "0", "no", "no", "Заполнить при необходимости"],
            ["installation", "Монтаж/пусконаладка", "0", "0", "no", "no", "Заполнить при необходимости"],
        ],
    )
    return [case_path, items_path, offers_path, expenses_path]


def load_case(case_dir: Path) -> tuple[TenderCase, list[LineItem], list[ProductOffer], list[CaseExpense]]:
    payload = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    tender_case = TenderCase(
        case_id=str(payload.get("case_id") or case_dir.name),
        title=str(payload.get("title") or ""),
        tender_number=str(payload.get("tender_number") or ""),
        source_url=str(payload.get("source_url") or ""),
        law=str(payload.get("law") or ""),
        customer=str(payload.get("customer") or ""),
        region=str(payload.get("region") or ""),
        delivery_address=str(payload.get("delivery_address") or ""),
        nmck=optional_decimal(payload.get("nmck")),
        planned_bid=optional_decimal(payload.get("planned_bid")),
        payment_days=int(payload.get("payment_days") or 0),
        delivery_days=optional_int(payload.get("delivery_days")),
        entity=str(payload.get("entity") or "auto"),
        requires_installation=parse_bool(payload.get("requires_installation")),
        notes=str(payload.get("notes") or ""),
    )
    items = [
        LineItem(
            line_id=row["line_id"].strip(),
            name=row["name"].strip(),
            quantity=required_decimal(row["quantity"], field_name="items.quantity"),
            unit=(row.get("unit") or "шт.").strip(),
            required_specs=(row.get("required_specs") or "").strip(),
            mandatory=parse_bool(row.get("mandatory", "yes")),
        )
        for row in _read_csv(case_dir / "items.csv")
        if (row.get("line_id") or "").strip() and (row.get("name") or "").strip()
    ]
    offers = [
        ProductOffer(
            line_id=row["line_id"].strip(),
            supplier=(row.get("supplier") or "").strip(),
            sku=(row.get("sku") or "").strip(),
            product_name=(row.get("product_name") or "").strip(),
            unit_cost_gross=required_decimal(row["unit_cost_gross"], field_name="offers.unit_cost_gross"),
            compliance_status=_compliance(row.get("compliance_status")),
            selected=parse_bool(row.get("selected")),
            stock=(row.get("stock") or "").strip(),
            lead_days=optional_int(row.get("lead_days")),
            vat_rate=optional_decimal(row.get("vat_rate")) or VAT_22,
            source_url=(row.get("source_url") or "").strip(),
            evidence=(row.get("evidence") or "").strip(),
            notes=(row.get("notes") or "").strip(),
        )
        for row in _read_csv(case_dir / "offers.csv")
        if (row.get("line_id") or "").strip() and (row.get("product_name") or "").strip()
    ]
    expenses = [
        CaseExpense(
            category=(row.get("category") or "other").strip(),
            description=(row.get("description") or "").strip(),
            amount_gross=required_decimal(row.get("amount_gross") or "0", field_name="expenses.amount_gross"),
            vat_rate=optional_decimal(row.get("vat_rate")) or ZERO,
            vat_reclaimable=parse_bool(row.get("vat_reclaimable")),
            confirmed=parse_bool(row.get("confirmed")),
            notes=(row.get("notes") or "").strip(),
        )
        for row in _read_csv(case_dir / "expenses.csv")
        if (row.get("category") or "").strip()
    ]
    return tender_case, items, offers, expenses


def calculate_case(
    tender_case: TenderCase,
    items: list[LineItem],
    offers: list[ProductOffer],
    expenses: list[CaseExpense],
    rules: BusinessRules | None = None,
) -> CaseEconomics:
    active_rules = rules or BusinessRules()
    selected_lines = [SelectedLine(item, _select_offer(item, offers)) for item in items]
    procurement_gross = money(sum((line.total_cost_gross for line in selected_lines), ZERO))
    expenses_gross = money(sum((expense.amount_gross for expense in expenses), ZERO))
    target_price = money(procurement_gross * (Decimal("1") + active_rules.target_markup) + expenses_gross)
    viable_price = money(procurement_gross * (Decimal("1") + active_rules.viable_markup) + expenses_gross)
    hard_floor_price = money(procurement_gross * (Decimal("1") + active_rules.hard_floor_markup) + expenses_gross)
    assessment_price = tender_case.planned_bid or tender_case.nmck
    risks, questions = _collect_risks(tender_case, selected_lines, expenses, active_rules)
    decision, reason = _decision(selected_lines, procurement_gross, assessment_price, target_price, viable_price, hard_floor_price)
    if risks and decision == "ready":
        decision = "manual_review"
        reason = "Экономика проходит, но остаются неподтвержденные риски."
    entity_scenarios = []
    if assessment_price is not None:
        entity_scenarios = [
            _entity_economics("ООО ТЛТ (ОСНО, НДС 22%)", assessment_price, selected_lines, expenses, vat_payer=True),
            _entity_economics(
                "ИП УСН Д-Р (ставка 10%)",
                assessment_price,
                selected_lines,
                expenses,
                vat_payer=False,
                usn_rate=Decimal("0.10"),
            ),
            _entity_economics(
                "ИП УСН Д-Р (ставка 15%)",
                assessment_price,
                selected_lines,
                expenses,
                vat_payer=False,
                usn_rate=Decimal("0.15"),
            ),
        ]
    return CaseEconomics(
        selected_lines=selected_lines,
        expenses=expenses,
        procurement_gross=procurement_gross,
        expenses_gross=expenses_gross,
        target_price=target_price,
        viable_price=viable_price,
        hard_floor_price=hard_floor_price,
        assessment_price=assessment_price,
        headroom_to_target=money(assessment_price - target_price) if assessment_price is not None else None,
        target_discount_from_nmck=_discount_from_nmck(tender_case.nmck, target_price),
        viable_discount_from_nmck=_discount_from_nmck(tender_case.nmck, viable_price),
        hard_floor_discount_from_nmck=_discount_from_nmck(tender_case.nmck, hard_floor_price),
        decision=decision,
        decision_reason=reason,
        risks=risks,
        questions=questions,
        entity_scenarios=entity_scenarios,
    )


def slugify_case_id(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "-", value.strip()).strip("-")
    return normalized[:80] or "tender-case"


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def required_decimal(value: object, *, field_name: str) -> Decimal:
    parsed = optional_decimal(value)
    if parsed is None:
        raise ValueError(f"Не заполнено числовое поле {field_name}")
    return parsed


def optional_decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное число: {value!r}") from exc


def optional_int(value: object) -> int | None:
    parsed = optional_decimal(value)
    return int(parsed) if parsed is not None else None


def parse_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "да", "д", "+"}


def _select_offer(item: LineItem, offers: list[ProductOffer]) -> ProductOffer | None:
    candidates = [offer for offer in offers if offer.line_id == item.line_id]
    manually_selected = [offer for offer in candidates if offer.selected]
    if manually_selected:
        return min(manually_selected, key=lambda offer: offer.unit_cost_gross)
    eligible = [offer for offer in candidates if offer.compliance_status in {"exact", "compliant"}]
    return min(eligible, key=lambda offer: offer.unit_cost_gross) if eligible else None


def _collect_risks(
    tender_case: TenderCase,
    selected_lines: list[SelectedLine],
    expenses: list[CaseExpense],
    rules: BusinessRules,
) -> tuple[list[str], list[str]]:
    risks: list[str] = []
    questions: list[str] = []
    region = f"{tender_case.region} {tender_case.delivery_address}".lower()
    if any(term in region for term in rules.individual_logistics_regions):
        delivery = [expense for expense in expenses if expense.category == "delivery"]
        if not delivery or not any(expense.confirmed and expense.amount_gross > ZERO for expense in delivery):
            risks.append("Для нового региона не подтверждена стоимость доставки.")
            questions.append("Получить отдельный расчет доставки до точного адреса поставки.")
    if tender_case.requires_installation:
        installation = [expense for expense in expenses if expense.category == "installation"]
        if not installation or not any(expense.confirmed and expense.amount_gross > ZERO for expense in installation):
            risks.append("Требуется монтаж, но его стоимость не подтверждена.")
            questions.append("Уточнить состав работ и получить расчет монтажа/пусконаладки.")
    if tender_case.payment_days > rules.tolerated_payment_days:
        risks.append(f"Срок оплаты {tender_case.payment_days} дней превышает обычный допустимый срок.")
    for line in selected_lines:
        if line.offer is None and line.item.mandatory:
            questions.append(f"Найти соответствующий товар по позиции {line.item.line_id}: {line.item.name}.")
            continue
        if line.offer is None:
            continue
        if line.offer.compliance_status == "conditional":
            risks.append(f"Позиция {line.item.line_id} соответствует условно и требует согласования.")
        if not line.offer.evidence:
            risks.append(f"По позиции {line.item.line_id} нет документального подтверждения характеристик.")
        if (
            tender_case.delivery_days is not None
            and line.offer.lead_days is not None
            and line.offer.lead_days > tender_case.delivery_days
        ):
            risks.append(f"Срок поставщика по позиции {line.item.line_id} превышает срок поставки по контракту.")
    for expense in expenses:
        if expense.amount_gross > ZERO and not expense.confirmed:
            risks.append(f"Расход «{expense.description or expense.category}» пока не подтвержден.")
    return _unique(risks), _unique(questions)


def _decision(
    lines: list[SelectedLine],
    procurement_gross: Decimal,
    assessment_price: Decimal | None,
    target_price: Decimal,
    viable_price: Decimal,
    hard_floor_price: Decimal,
) -> tuple[DecisionStatus, str]:
    missing = [line for line in lines if line.item.mandatory and line.offer is None]
    conditional = [line for line in lines if line.offer and line.offer.compliance_status == "conditional"]
    if not lines:
        return "blocked", "Не заполнены позиции закупки."
    if missing:
        return "blocked", f"Нет подтвержденного товара по обязательным позициям: {', '.join(line.item.line_id for line in missing)}."
    if conditional:
        return "manual_review", "Есть условно соответствующие позиции, необходимо решение владельца."
    if procurement_gross <= ZERO:
        return "blocked", "Не заполнена закупочная стоимость товаров."
    if assessment_price is None:
        return "manual_review", "Не указана НМЦК или плановая цена предложения."
    if assessment_price < hard_floor_price:
        return "stop", "Цена ниже жесткого порога 12% наценки после учета расходов."
    if assessment_price < viable_price:
        return "manual_review", "Цена находится в зоне ручного решения: наценка от 12% до 15%."
    if assessment_price < target_price:
        return "manual_review", "Цена выше рабочего порога 15%, но ниже целевой наценки 30%."
    return "ready", "Целевая наценка 30% достижима при указанной цене."


def _entity_economics(
    entity: str,
    sale_price_gross: Decimal,
    lines: list[SelectedLine],
    expenses: list[CaseExpense],
    *,
    vat_payer: bool,
    usn_rate: Decimal | None = None,
) -> EntityEconomics:
    procurement = ZERO
    for line in lines:
        if line.offer is None:
            continue
        gross = line.total_cost_gross
        procurement += _net_of_vat(gross, line.offer.vat_rate) if vat_payer else gross
    expense_cost = ZERO
    for expense in expenses:
        if vat_payer and expense.vat_reclaimable:
            expense_cost += _net_of_vat(expense.amount_gross, expense.vat_rate)
        else:
            expense_cost += expense.amount_gross
    revenue_net = _net_of_vat(sale_price_gross, VAT_22) if vat_payer else sale_price_gross
    profit = money(revenue_net - procurement - expense_cost)
    total_cost = procurement + expense_cost
    profit_rate = (profit / total_cost).quantize(Decimal("0.0001")) if total_cost > ZERO else None
    estimated_tax = None
    minimum_tax_reference = None
    profit_after_tax = None
    if usn_rate is not None:
        standard_tax = money(max(profit, ZERO) * usn_rate)
        minimum_tax_reference = money(revenue_net * Decimal("0.01"))
        estimated_tax = max(standard_tax, minimum_tax_reference)
        profit_after_tax = money(profit - estimated_tax)
    if vat_payer:
        note = "Расчет до налога на прибыль; входной НДС принят к вычету только по отмеченным расходам."
    else:
        note = (
            "УСН «доходы минус расходы», ИП без НДС; входной НДС включен в стоимость. "
            "Налог показан как оценка по одному тендеру: фактический УСН и минимальный налог считаются нарастающим итогом за год, "
            "а расходы должны соответствовать требованиям налогового учета."
        )
    return EntityEconomics(
        entity=entity,
        sale_price_gross=money(sale_price_gross),
        revenue_net=money(revenue_net),
        procurement_cost=money(procurement),
        expense_cost=money(expense_cost),
        profit_before_income_tax=profit,
        estimated_tax=estimated_tax,
        minimum_tax_reference=minimum_tax_reference,
        profit_after_estimated_tax=profit_after_tax,
        profit_rate_on_cost=profit_rate,
        note=note,
    )


def _net_of_vat(gross: Decimal, vat_rate: Decimal) -> Decimal:
    if vat_rate <= ZERO:
        return gross
    return gross / (Decimal("1") + vat_rate)


def _discount_from_nmck(nmck: Decimal | None, threshold_price: Decimal) -> Decimal | None:
    if nmck is None or nmck <= ZERO:
        return None
    return ((nmck - threshold_price) / nmck).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _compliance(value: object) -> ComplianceStatus:
    normalized = str(value or "").strip().lower()
    aliases = {
        "точное": "exact",
        "exact": "exact",
        "соответствует": "compliant",
        "compliant": "compliant",
        "условно": "conditional",
        "conditional": "conditional",
        "не соответствует": "not_compliant",
        "not_compliant": "not_compliant",
        "не найден": "not_found",
        "not_found": "not_found",
    }
    if normalized not in aliases:
        raise ValueError(f"Неизвестный compliance_status: {value!r}")
    return aliases[normalized]  # type: ignore[return-value]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
