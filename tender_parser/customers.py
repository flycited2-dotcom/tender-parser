from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from tender_parser.models import TenderRecord


CUSTOMER_HEADERS = [
    "Ключ организации",
    "Организация",
    "Тип",
    "Регион",
    "ИНН",
    "Юридический адрес",
    "Фактический / почтовый адрес",
    "Общий e-mail",
    "Телефон",
    "Контактное лицо / должность",
    "Официальный сайт",
    "Источник контактов",
    "Закупка-основание",
    "Дата проверки",
    "Статус контакта",
    "Примечание",
]

CONTACT_STATUS_DEFAULT = "Нужно проверить"
CONTACT_STATUSES = {
    "Новый",
    "Нужно проверить",
    "Проверен",
    "Готов к обращению",
    "Не писать",
    "Отписка",
}

_TARGET_REGIONS = (
    ("Республика Крым", ("республика крым", "крым", "симферопол", "керч", "ялт", "евпатори")),
    ("Севастополь", ("севастопол",)),
    ("Запорожская область", ("запорож", "мелитопол", "бердянск", "энергодар")),
    ("Херсонская область", ("херсон", "геническ", "новая каховка", "скадовск")),
)

_PUBLIC_MARKERS = (
    "государственн",
    "муниципальн",
    "бюджетн",
    "казенн",
    "автономн",
    "администрац",
    "правительств",
    "министерств",
    "департамент",
    "управление",
    "казначейств",
    "верховный суд",
    "городской суд",
    "районный суд",
    "фгбу",
    "фку",
    "фгбоу",
    "фгаоу",
    "фгуп",
    "гбу",
    "гку",
    "гау",
    "гуп",
    "мбу",
    "мку",
    "муп",
    "мбоу",
    "гбоу",
)

_PRIVATE_MARKERS = (
    "общество с ограниченной ответственностью",
    "акционерное общество",
    "публичное акционерное общество",
    "автономная некоммерческая организация",
    "частное учреждение",
    "частное образовательное учреждение",
    "негосударственное образовательное учреждение",
    "индивидуальный предприниматель",
    "ооо ",
    "ао ",
    "пао ",
    "ано ",
    "чоу ",
    "ноу ",
    "ип ",
)


def build_customer_registry(
    tenders: Iterable[TenderRecord],
    existing_rows: list[list[object]],
) -> list[list[object]]:
    """Merge public-sector tender customers into a manually enrichable CRM table."""

    existing = {
        str(row[0]): _pad(row)
        for row in existing_rows
        if row
        and str(row[0] or "").strip()
    }
    by_key: dict[str, TenderRecord] = {}
    for tender in tenders:
        if not tender.customer or not is_potential_customer(tender.customer):
            continue
        region = customer_region(tender)
        if not region:
            continue
        key = organization_key(clean_customer_name(tender.customer))
        current = by_key.get(key)
        if current is None or _candidate_score(tender) > _candidate_score(current):
            by_key[key] = tender

    result: dict[str, list[object]] = dict(existing)
    for key, tender in by_key.items():
        previous = existing.get(key, [""] * len(CUSTOMER_HEADERS))
        row = _pad(previous)
        row[0] = key
        row[1] = clean_customer_name(tender.customer or "")
        row[2] = organization_type(str(row[1]))
        row[3] = customer_region(tender)
        row[12] = tender.url
        if not row[14] or str(row[14]) not in CONTACT_STATUSES:
            row[14] = CONTACT_STATUS_DEFAULT
        result[key] = row

    return sorted(
        result.values(),
        key=lambda row: (str(row[3] or ""), str(row[1] or "").casefold()),
    )


def organization_key(name: str) -> str:
    normalized = re.sub(r"[^0-9a-zа-яё]+", "", name.casefold())
    return normalized[:180]


def is_public_customer(name: str) -> bool:
    normalized = " ".join(name.casefold().replace("ё", "е").split())
    return (
        bool(normalized)
        and not is_private_customer(normalized)
        and any(marker in normalized for marker in _PUBLIC_MARKERS)
    )


def is_potential_customer(name: str) -> bool:
    """Accept any meaningful procurement customer, public or commercial."""

    normalized = " ".join(name.casefold().replace("ё", "е").split())
    if not normalized:
        return False
    placeholders = {
        "заказчик",
        "организатор",
        "не указано",
        "не указан",
        "информация отсутствует",
        "нет данных",
        "-",
        "подробнее",
        "организация",
        "республика крым",
        "крым",
        "севастополь",
        "запорожская область",
        "херсонская область",
        "краснодарский край",
    }
    if normalized in placeholders or normalized.startswith("не определен"):
        return False
    return sum(character.isalpha() for character in normalized) >= 3


def is_private_customer(name: str) -> bool:
    normalized = " ".join(name.casefold().replace("ё", "е").split())
    padded = f"{normalized} "
    return any(marker in padded for marker in _PRIVATE_MARKERS)


def organization_type(name: str) -> str:
    value = name.casefold().replace("ё", "е")
    compact = re.sub(r"[^0-9a-zа-я]+", " ", value).strip()
    tokens = set(compact.split())
    if "ип" in tokens or "индивидуальный предприниматель" in value:
        return "Индивидуальный предприниматель"
    if "ооо" in tokens or "общество с ограниченной ответственностью" in value:
        return "Коммерческая организация — ООО"
    if "пао" in tokens or "публичное акционерное общество" in value:
        return "Коммерческая организация — ПАО"
    if "ао" in tokens or "акционерное общество" in value:
        return "Коммерческая организация — АО"
    if "ано" in tokens or "автономная некоммерческая организация" in value:
        return "Некоммерческая организация — АНО"
    if "некоммерческая организация" in value or "фонд" in tokens:
        return "Некоммерческая организация / фонд"
    if "частное учреждение" in value or "чоу" in tokens or "ноу" in tokens:
        return "Частное учреждение"
    if "фгуп" in value or "гуп" in value or "муп" in value or "унитарн" in value:
        return "Государственное / муниципальное предприятие"
    if any(
        marker in value
        for marker in (
            "администрац",
            "правительств",
            "министерств",
            "департамент",
            "управлен",
            "комитет",
            "служба",
            "казначейств",
            "таможн",
            "законодательное собрание",
            "счетная палата",
            "агентств",
            "уфнс",
            "уфсин",
            "росфинмониторинг",
            "росздравнадзор",
            "росрыболовств",
        )
    ):
        return "Орган власти"
    if re.search(r"\bсуд(?:а|у|ом|е)?\b", value):
        return "Суд"
    if any(
        marker in value
        for marker in (
            "фгбоу",
            "фгаоу",
            "гбоу",
            "мбоу",
            "фгбпоу",
            "гбпоу",
            "образовательн",
        )
    ):
        return "Образовательное учреждение"
    if "казенн" in value or any(marker in value for marker in ("фку", "гку", "мку")):
        return "Казённое учреждение"
    if "автономн" in value or "гау" in value:
        return "Автономное учреждение"
    if "бюджетн" in value or "учреждени" in value or tokens.intersection(
        {"гбу", "гбуз", "фгбу", "фбуз", "фгбун", "кгб", "фбу"}
    ):
        return "Бюджетное учреждение"
    return "Коммерческая / иная организация"


def customer_region(tender: TenderRecord) -> str:
    # Для CRM используем только явное поле региона/доказательство адреса доставки.
    # include_reason намеренно не берём: ошибочное ключевое совпадение не должно
    # превращать заказчика из другого субъекта в крымского.
    evidence = " ".join(
        part for part in (tender.region or "", tender.delivery_region_evidence or "") if part
    ).casefold().replace("ё", "е")
    matched = [label for label, aliases in _TARGET_REGIONS if any(alias in evidence for alias in aliases)]
    return ", ".join(matched)


def compact_tender_region(tender: TenderRecord) -> str:
    region = customer_region(tender)
    if region:
        return region
    raw = " ".join((tender.region or "").split())
    return raw if len(raw) <= 120 else f"{raw[:117].rstrip()}…"


def _candidate_score(tender: TenderRecord) -> tuple[int, datetime]:
    return (
        int(bool(tender.customer)) + int(bool(tender.region)) + int(bool(tender.url)),
        tender.discovered_at or datetime.min,
    )


def clean_customer_name(value: str) -> str:
    cleaned = " ".join(value.split())
    # EIS highlights search matches with inline markup. Extracted text can
    # split a geographical word immediately before its ending.
    return re.sub(
        r"\b(РЕСПУБЛИК|ОБЛАСТ|СЕВАСТОПОЛ|ЗАПОРОЖСК|КРЫМСК)\s+(И|Я|ОЙ|ИЙ)\b",
        lambda match: f"{match.group(1)}{match.group(2)}",
        cleaned,
        flags=re.IGNORECASE,
    )


def _pad(row: list[object]) -> list[object]:
    return [*row[: len(CUSTOMER_HEADERS)], *([""] * max(0, len(CUSTOMER_HEADERS) - len(row)))]
