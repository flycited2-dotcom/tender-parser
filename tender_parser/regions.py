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
    ],
    "Запорожская область": [
        "запорожск",
        "запорожь",
        "мелитопол",
        "бердянск",
        "энергодар",
        "токмак",
    ],
    "Херсонская область": [
        "херсон",
        "геническ",
        "скадовск",
        "каховк",
    ],
}

# Топонимы-двойники и адресные ложные сигналы: вырезаются из текста до сканирования.
NEGATIVE_PATTERNS = [
    r"крымск(?:ого|ий)\s+вал[а-я]*",          # улица Крымский Вал (Москва)
    r"крымск(?:ий|ого|ому)\s+мост[а-я]*",     # Крымский мост (Москва)
    r"(?<![\w])крымск[аеи]?(?![\w])",          # г. Крымск (Краснодарский край), но не «крымская»
    r"(?<![\w])херсонес[\w-]*",                # Херсонес (Севастополь, не Херсонская область)
    r"белогорск[а-я]*[^а-я]{0,4}амурск[а-я]*",  # Белогорск Амурской области
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


def region_bucket(text: str | None) -> str:
    canonical = detect_region(text)
    if canonical is None:
        return ""
    return REGION_BUCKETS.get(canonical, "")
