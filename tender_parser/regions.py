from __future__ import annotations

import re

from tender_parser import config
from tender_parser.text import normalize_text


# Канонический регион -> варианты написания. Вариант матчится от границы слова
# с любым продолжением, поэтому стем "севастопол" покрывает все падежи.
# Порядок важен: конкретные города раньше общих регионов.
REGION_VARIANTS: dict[str, list[str]] = {
    "Симферополь": ["симферопол"],
    "Севастополь": ["севастопол"],
    "Республика Крым": [
        "республика крым",
        "республики крым",
        "республике крым",
        "республику крым",
        "республикой крым",
        "респ. крым",
        "респ крым",
    ],
    "Крым": [
        "крым",
        "ялта", "ялте", "ялты", "ялту",
        "керчь", "керчи",
        "евпатори",
        "феодоси",
        "джанко",
        "алушт",
        "бахчисара",
        "красноперекопск",
        "армянск",
        "белогорск",
        "саки", "сакский",
        "щелкино",
        "судак",
        "алупк",
        "старый крым",
        "коктебел",
        "гурзуф",
        "красногвардейск",
        "черноморское",
    ],
    "Запорожская область": [
        "запорожск",
        "запорожь",
        "мелитопол",
        "бердянск",
        "энергодар",
        "токмак",
        "каменка-днепровск",
        "днепрорудн",
    ],
    "Херсонская область": [
        "херсон",
        "геническ",
        "скадовск",
        "каховк",
        "новая каховк",
        "алешк",
        "голая пристань",
        "чаплинк",
        "каланчак",
    ],
}

# Явные нецелевые субъекты и крупные города. Эти варианты используются только
# когда ни в одном сильном поле карточки не найден целевой регион. Поэтому адрес
# заказчика из Москвы не перебивает явно указанную доставку в Севастополь.
NON_TARGET_REGION_VARIANTS = [
    "республика адыге",
    "башкорт",
    "бурят",
    "республика алтай",
    "дагестан",
    "ингуш",
    "кабардино-балкар",
    "калмык",
    "карачаево-черкес",
    "карели",
    "республика коми",
    "марий эл",
    "мордов",
    "республика саха",
    "якут",
    "северн осети",
    "татарстан",
    "республика тыва",
    "удмурт",
    "хакас",
    "чечен",
    "чуваш",
    "алтайск",
    "краснодар",
    "краснояр",
    "ставропол",
    "хабаров",
    "камчат",
    "пермск",
    "забайкал",
    "амурск",
    "архангел",
    "астрахан",
    "белгород",
    "брянск",
    "владимирск",
    "волгоград",
    "вологод",
    "воронеж",
    "ивановск",
    "иркутск",
    "калининград",
    "калужск",
    "кемеров",
    "кировск",
    "костром",
    "курган",
    "курск",
    "ленинград",
    "липецк",
    "магадан",
    "московск",
    "мурман",
    "нижегород",
    "новгородск",
    "новосибирск",
    "омск",
    "оренбург",
    "орловск",
    "пензен",
    "псков",
    "ростов",
    "рязан",
    "самар",
    "саратов",
    "сахалин",
    "свердлов",
    "смоленск",
    "тамбов",
    "тверск",
    "томск",
    "тульск",
    "тюмен",
    "ульяновск",
    "челябинск",
    "ярославск",
    "еврейск автоном",
    "ненецк автоном",
    "ханты-мансийск",
    "чукотск",
    "ямало-ненецк",
    "донецк",
    "луганск",
    "москва",
    "санкт-петербург",
    "екатеринбург",
    "казань",
    "уфа",
    "владивосток",
]

DELIVERY_CONTEXT_PATTERNS = [
    r"(?:адрес|место|регион|территория)\s+(?:места\s+)?(?:поставк|доставк)[а-я]*[^;]{0,180}",
    r"доставк[а-я]*\s+(?:до|в|на|по\s+адресу)[^;]{0,180}",
    r"поставить\s+(?:в|на|по\s+адресу)[^;]{0,180}",
]

# Топонимы-двойники и адресные ложные сигналы: вырезаются из текста до сканирования.
NEGATIVE_PATTERNS = [
    r"крымск(?:ого|ий)\s+вал[а-я]*",          # улица Крымский Вал (Москва)
    r"крымск(?:ий|ого|ому)\s+мост[а-я]*",     # Крымский мост (Москва)
    r"(?:ул(?:ица)?\.?|проспект|переулок)\s+крымск[а-я]*",  # адрес с названием «Крымская»
    r"крымск[а-я]*\s+(?:ул(?:ица)?|проспект|переулок)",
    r"(?<![\w])крымск[аеи]?(?![\w])",          # г. Крымск (Краснодарский край), но не «крымская»
    r"(?<![\w])херсонес[\w-]*",                # Херсонес (Севастополь, не Херсонская область)
    r"белогорск[а-я]*.{0,100}амурск[а-я]*",     # Белогорск Амурской области
]

# Вариант матчится, только если сразу после него нет запрещённого продолжения.
VARIANT_SUFFIX_GUARDS = {
    "армянск": "и",  # «армянский коньяк» — не город Армянск
}

REGION_BUCKETS: dict[str, str] = {
    "Симферополь": "crimea",
    "Севастополь": "sevastopol",
    "Республика Крым": "crimea",
    "Крым": "crimea",
    "Запорожская область": "zaporizhzhia",
    "Херсонская область": "kherson",
}

def region_priority_rank(*texts: str | None) -> int:
    """Return the target-region priority: Crimea/Sevastopol, then new regions."""

    values = [value for value in texts if value and value.strip()]
    if not values:
        return 3
    combined = " ".join(values)
    canonical = detect_region(combined)
    if canonical in {"Симферополь", "Севастополь", "Республика Крым", "Крым"}:
        return 0
    if canonical in {"Запорожская область", "Херсонская область"}:
        return 1
    return 2


def detect_region(text: str | None) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    for pattern in NEGATIVE_PATTERNS:
        normalized = re.sub(pattern, " ", normalized)
    for canonical, variants in REGION_VARIANTS.items():
        for variant in variants:
            guard = VARIANT_SUFFIX_GUARDS.get(variant)
            suffix = f"(?!{guard})" if guard else ""
            if re.search(rf"(?<![\w]){re.escape(variant)}{suffix}", normalized):
                return canonical
    # Пользовательские регионы из Excel поддерживаются по полному названию.
    for configured_region in config.SEARCH_REGION_TERMS:
        variant = normalize_text(configured_region)
        if variant and re.search(rf"(?<![\w]){re.escape(variant)}(?![\w])", normalized):
            return configured_region
    return None


def detect_delivery_region(text: str | None) -> str | None:
    """Находит целевой регион именно в контексте места поставки/доставки."""
    normalized = normalize_text(text)
    if not normalized:
        return None
    for pattern in DELIVERY_CONTEXT_PATTERNS:
        for match in re.finditer(pattern, normalized):
            region = detect_region(match.group(0))
            if region:
                return region
    return None


def detect_non_target_region(text: str | None) -> str | None:
    """Возвращает явный нецелевой регион/город, но не при наличии цели."""
    normalized = normalize_text(text)
    if not normalized or detect_region(normalized):
        return None
    for variant in NON_TARGET_REGION_VARIANTS:
        if re.search(rf"(?<![\w]){re.escape(variant)}", normalized):
            return variant
    return None


def region_bucket(text: str | None) -> str:
    canonical = detect_region(text)
    if canonical is None:
        return ""
    return REGION_BUCKETS.get(canonical, "")
