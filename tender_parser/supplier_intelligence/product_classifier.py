"""Config-driven product category and model extraction from mail evidence."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .extraction import split_quoted


DEFAULT_CATEGORIES_PATH = Path(__file__).with_name("config") / "product_categories.yaml"


@lru_cache(maxsize=8)
def _load_categories(config_path: str | Path | None) -> list[dict[str, Any]]:
    """Load the JSON-compatible YAML 1.2 taxonomy using Python's stdlib."""
    path = Path(config_path) if config_path is not None else DEFAULT_CATEGORIES_PATH
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or not isinstance(value.get("categories"), list):
        raise ValueError(f"Invalid product taxonomy: {path}")
    rules = value["categories"]
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("path"), list) or not 1 <= len(rule["path"]) <= 4:
            raise ValueError(f"Invalid product category rule in {path}")
        key = tuple(str(part) for part in rule["path"])
        if key not in merged:
            merged[key] = {**rule, "terms": list(rule.get("terms", [])),
                           "brands": list(rule.get("brands", [])),
                           "model_patterns": list(rule.get("model_patterns", []))}
            continue
        target = merged[key]
        for field in ("terms", "brands", "model_patterns"):
            target[field] = list(dict.fromkeys([*target[field], *rule.get(field, [])]))
        for field in ("brand_as_level3", "model_as_level4"):
            target[field] = bool(target.get(field) or rule.get(field))
    return list(merged.values())


def category_groups(config_path: str | Path | None = None) -> list[str]:
    """All top-level procurement groups defined in the editable taxonomy."""
    return sorted({str(rule["path"][0]) for rule in _load_categories(config_path)})


@lru_cache(maxsize=2048)
def _term_pattern(term: str) -> re.Pattern[str]:
    tokens = [re.escape(token).replace(r"\*", r"\w*") for token in term.split()]
    return re.compile(r"(?<!\w)" + r"[\s_-]+".join(tokens) + r"(?!\w)", re.I)


def _matching_term(text: str, rule: dict[str, Any]) -> str | None:
    for term in rule.get("terms", []):
        match = _term_pattern(term).search(text)
        if match:
            return match.group()
    return None


def _models(text: str, rule: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for expression in rule.get("model_patterns", []):
        for match in re.finditer(expression, text, re.I):
            model = match.group().strip()
            if model.casefold() not in {item.casefold() for item in found}:
                found.append(model)
    return found


def _brand(text: str, rule: dict[str, Any]) -> str | None:
    for brand in rule.get("brands", []):
        if re.search(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", text, re.I):
            return brand
    return None


def _product_name(line: str, matched_term: str | None, brand: str | None, model: str | None) -> str:
    cleaned = re.sub(r"\.(?:pdf|xls|xlsx|doc|docx)$", "", line, flags=re.I)
    cleaned = re.sub(r"[_|]+", " ", cleaned)
    cleaned = re.sub(r"^(?:re:|fw:|fwd:|кп|запрос\s+цен[ы]?\s+на|просим\s+(?:предоставить|направить)\s+(?:кп|цену)\s+на)\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^\d+[.)]\s*", "", cleaned)
    cleaned = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:шт\.?|штук|компл\.?)\b.*$", "", cleaned, flags=re.I)
    cleaned = " ".join(cleaned.strip(" \t,;:-–—").split())
    if model and brand:
        # A compact literal product label is preferable to a whole mail sentence.
        return f"{brand} {model}"
    if model and len(cleaned) > 100:
        return model
    if cleaned and len(cleaned) <= 120:
        return cleaned
    return matched_term or model or ""


def classify_products(
    subject: str | None,
    body: str | None,
    attachment_names: list[str] | tuple[str, ...] | None = None,
    *,
    config_path: str | Path | None = None,
) -> list[dict[str, object]]:
    """Return products backed by a term/model in fresh mail or attachment names.

    Quoted history alone cannot create a product association. Category paths
    and recognition terms live in ``product_categories.yaml``.
    """
    parts = split_quoted(body)
    sources: list[tuple[str, str]] = []
    if subject:
        sources.append(("subject", subject))
    sources.extend(("body", line) for line in parts["new_message_body"].splitlines() if line.strip())
    sources.extend(("attachment", name) for name in (attachment_names or []) if name)
    results: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for rule in _load_categories(config_path):
        evidence: list[tuple[str, str, str | None, list[str]]] = []
        for source_type, line in sources:
            term = _matching_term(line, rule)
            models = _models(line, rule)
            if term or models:
                evidence.append((source_type, line, term, models))
        if not evidence:
            continue
        # The most specific evidence wins: a named model in the new body or
        # subject takes precedence over a generic attachment filename.
        evidence.sort(key=lambda item: (bool(item[3]), item[0] == "subject", item[0] == "body"), reverse=True)
        all_models = list(dict.fromkeys(model for _, _, _, models in evidence for model in models))
        if not all_models:
            all_models = [""]
        for model in all_models:
            relevant = next((item for item in evidence if model and model in item[3]), evidence[0])
            source_type, line, term, _ = relevant
            brand = _brand(line, rule) or _brand(" ".join(text for _, text in sources), rule)
            path = list(rule["path"])
            if rule.get("brand_as_level3") and brand and len(path) == 2:
                path.append(brand)
            if rule.get("model_as_level4") and model and len(path) == 3:
                path.append(model)
            path = (path + [None] * 4)[:4]
            if term and model:
                confidence = 0.94 if source_type != "attachment" else 0.82
            elif model:
                confidence = 0.81 if source_type != "attachment" else 0.70
            elif term:
                confidence = 0.82 if source_type == "subject" else 0.78 if source_type == "body" else 0.67
            else:
                confidence = 0.0
            name = _product_name(line, term, brand, model or None)
            key = (str(path[0]), str(path[1]), model.casefold() if model else name.casefold())
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "category_l1": path[0],
                    "category_l2": path[1],
                    "category_l3": path[2],
                    "category_l4": path[3],
                    "brand": brand,
                    "manufacturer": None,
                    "model": model or None,
                    "article": None,
                    "product_name": name,
                    "confidence": confidence,
                }
            )
    # A specific subcategory supersedes the same category's generic entry.
    specific = {(item["category_l1"], item["category_l2"]) for item in results if item["category_l3"]}
    results = [
        item for item in results
        if item["category_l3"] or (item["category_l1"], item["category_l2"]) not in specific
    ]
    return results
