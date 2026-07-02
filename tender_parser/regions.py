from __future__ import annotations

import re

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
    for canonical, variants in REGION_VARIANTS.items():
        for variant in variants:
            if re.search(rf"(?<![\w]){re.escape(variant)}", normalized):
                return canonical
    return None


def region_bucket(text: str | None) -> str:
    canonical = detect_region(text)
    if canonical is None:
        return ""
    return REGION_BUCKETS.get(canonical, "")
