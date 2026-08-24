from __future__ import annotations

import re


# The classifier intentionally covers both equipment and installation wording.
# Final classification remains semantic: these terms are signals, not a closed list.
_CLIMATE_PATTERNS = (
    r"\bкондиционер\w*\b",
    r"\bсплит[ -]?систем\w*\b",
    r"\bмульти[ -]?сплит\w*\b",
    r"\bvrf\b|\bvrv\b",
    r"\bчиллер\w*\b|\bфанкойл\w*\b",
    r"\bклиматическ\w*\s+(?:техник\w*|оборудован\w*|установк\w*)\b",
    r"\bвентил(?:яци\w*|ятор\w*)\b",
    r"\bприточн\w*\s+(?:установк\w*|вентиляци\w*)\b",
    r"\bвытяжн\w*\s+(?:установк\w*|вентиляци\w*)\b",
    r"\bтеплов\w*\s+(?:насос\w*|завес\w*|пушк\w*)\b",
    r"\b(?:котел|котлы|котла|котельн\w*)\b",
    r"\b(?:радиатор\w*|конвектор\w*|обогревател\w*)\b",
    r"\bвоздухоохладител\w*\b|\bруфтоп\w*\b",
    r"\bмонтаж\w*\s+(?:кондиционер\w*|сплит\w*|вентиляци\w*)\b",
    r"\bустановк\w*\s+(?:кондиционер\w*|сплит\w*|вентиляци\w*)\b",
)


def is_climate_request(*values: str) -> bool:
    """Return True when a tender line belongs to climate/HVAC supply or works."""
    text = " ".join(value for value in values if value).casefold().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _CLIMATE_PATTERNS)
