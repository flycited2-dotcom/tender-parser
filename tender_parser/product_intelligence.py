from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class OemReference:
    device_model: str
    item_kind: str
    parts: tuple[str, ...]
    evidence_url: str
    note: str = ""


OEM_REFERENCES: tuple[OemReference, ...] = (
    OemReference(
        "HL-L5210DW",
        "drum_unit",
        ("DR3600", "DR-3600"),
        "https://support.brother.com/g/b/colist.aspx?c=gb&cao=dr&lang=en&pfs=1&prod=hll5210dw_us_eu_as",
        "Brother указывает DR3600 как фотобарабан для HL-L5210DW.",
    ),
    OemReference(
        "HL-L5210DW",
        "drum_chip",
        ("DR3600", "DR-3600"),
        "https://support.brother.com/g/b/colist.aspx?c=gb&cao=dr&lang=en&pfs=1&prod=hll5210dw_us_eu_as",
        "Для поиска отдельного чипа используется артикул совместимого драм-юнита.",
    ),
    OemReference(
        "HL-L5210DW",
        "toner_chip",
        ("TN3600XXL", "TN-3600XXL"),
        "https://support.brother.com/g/b/colist.aspx?c=es&cao=tn&lang=es&prod=hll5210dw_us_eu_as",
        "Ресурсу 11 000 страниц соответствует TN3600XXL.",
    ),
)


MODEL_PATTERN = re.compile(
    r"\b(?:Brother\s+)?HL-[A-Z0-9-]+\b|\b(?:Kyocera\s+)?(?:ECOSYS\s+)?M\d+[A-Z0-9-]*\b",
    flags=re.IGNORECASE,
)


def classify_item_kind(name: str) -> str:
    normalized = _normalize(name)
    if "чип" in normalized and ("тонер" in normalized or "картридж" in normalized):
        return "toner_chip"
    if "чип" in normalized and ("фотобарабан" in normalized or "драм" in normalized):
        return "drum_chip"
    if "термоплен" in normalized:
        return "fuser_film"
    if "термоблок" in normalized or "фьюзер" in normalized or "печка" in normalized:
        return "fuser"
    if "фотобарабан" in normalized or "драм юнит" in normalized:
        return "drum_unit"
    return "generic"


def extract_device_models(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in MODEL_PATTERN.finditer(text):
        value = " ".join(match.group().split())
        if value.lower().startswith("brother "):
            value = value[8:]
        if value.lower().startswith("kyocera "):
            value = value[8:]
        if value.lower().startswith("ecosys "):
            value = value[7:]
        if value.upper() not in {item.upper() for item in values}:
            values.append(value)
    return tuple(values)


def oem_references(item_name: str, required_specs: str) -> tuple[OemReference, ...]:
    kind = classify_item_kind(item_name)
    models = {model.upper() for model in extract_device_models(required_specs)}
    return tuple(
        reference
        for reference in OEM_REFERENCES
        if reference.item_kind == kind and reference.device_model.upper() in models
    )


def build_search_queries(item_name: str, required_specs: str) -> tuple[str, ...]:
    clean_name = re.sub(r"\b\d{2}(?:\.\d{2}){2}\.\d{3}\b", " ", item_name)
    clean_name = " ".join(clean_name.replace("/", " ").split()).strip()
    kind = classify_item_kind(clean_name)
    models = extract_device_models(required_specs)
    model = models[0] if models else ""
    canonical_names = {
        "drum_unit": ("Фотобарабан", "Драм-юнит"),
        "drum_chip": ("Чип фотобарабана", "Чип драм-юнита"),
        "toner_chip": ("Чип тонер-картриджа", "Чип картриджа"),
        "fuser": ("Термоблок", "Фьюзер", "Печка"),
        "fuser_film": ("Термопленка",),
        "generic": (clean_name,),
    }
    queries: list[str] = []
    for label in canonical_names[kind]:
        queries.append(" ".join(part for part in (label, model) if part))
    for reference in oem_references(item_name, required_specs):
        for part in reference.parts:
            label = canonical_names[kind][0]
            queries.extend((f"{label} {part}", part))
    if clean_name and kind == "generic":
        queries.insert(0, " ".join(part for part in (clean_name, model) if part))
    return tuple(dict.fromkeys(_unique_query(query) for query in queries if query.strip()))


def alternative_search_links(item_name: str, required_specs: str) -> tuple[dict[str, str], ...]:
    queries = build_search_queries(item_name, required_specs)
    if not queries:
        return ()
    query = queries[0]
    references = oem_references(item_name, required_specs)
    if references:
        query = f"купить {references[0].parts[0]} {classify_item_kind(item_name).replace('_', ' ')}"
    encoded = quote_plus(query)
    return (
        {"label": "Яндекс", "url": f"https://yandex.ru/search/?text={encoded}", "query": query},
        {"label": "Google", "url": f"https://www.google.com/search?q={encoded}", "query": query},
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.lower()).strip()


def _unique_query(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:500]
