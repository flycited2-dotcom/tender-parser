"""Evidence-first supplier lookup; no opaque or subjective rank score."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any

_STOPWORDS = {
    "по", "для", "на", "из", "под", "при", "нас", "нам", "мы", "у", "кто", "кому",
    "какие", "есть", "уже", "отправляли", "запросы", "запрос", "предлагал",
    "занимается", "предложение", "поставщики", "поставщик",
}


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\w-]+", value.casefold().replace("ё", "е"))
            if len(token) > 1 and token not in _STOPWORDS]


def _matches(text: str, terms: list[str]) -> bool:
    if not terms:
        return True
    words = _tokens(text)
    return all(any(word == term or len(term) >= 5 and word.startswith(term[:5]) for word in words)
               for term in terms)


def _fetch(db: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]


def find_suppliers(db: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    """Return matched suppliers and source facts, ordered by recent contact only."""
    suppliers = _fetch(db, "suppliers")
    categories = _fetch(db, "supplier_categories")
    products = _fetch(db, "supplier_products")
    contacts = _fetch(db, "contacts")
    interactions = _fetch(db, "interactions")
    quotes = _fetch(db, "quotes")
    terms = _tokens(query)
    if query.strip() and not terms:
        return []
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for name, values in (
        ("categories", categories), ("products", products), ("contacts", contacts),
        ("interactions", interactions), ("quotes", quotes),
    ):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in values:
            groups[row["supplier_id"]].append(row)
        grouped[name] = groups
    result: list[dict[str, Any]] = []
    for supplier in suppliers:
        sid = supplier["supplier_id"]
        if not sid:
            continue
        own_categories = grouped["categories"].get(sid, [])
        own_products = grouped["products"].get(sid, [])
        own_contacts = grouped["contacts"].get(sid, [])
        own_interactions = grouped["interactions"].get(sid, [])
        own_quotes = grouped["quotes"].get(sid, [])
        category_hits = [row for row in own_categories if _matches(" ".join(
            row[f"category_l{i}"] for i in range(1, 5)), terms)]
        product_hits = [row for row in own_products if _matches(" ".join(
            str(row[key]) for key in ("brand", "manufacturer", "model", "article", "product_name",
                                 "category_l1", "category_l2", "category_l3", "category_l4")), terms)]
        quote_hits = [row for row in own_quotes if _matches(" ".join(
            str(row[key]) for key in ("product_name", "brand", "model", "article")), terms)]
        name_hit = _matches(" ".join(str(supplier[key]) for key in
                                     ("company_name", "legal_name", "short_name")), terms)
        if terms and not (category_hits or product_hits or quote_hits or name_hit):
            continue
        # Return only evidence for the query, but keep interaction history for
        # determining whether a request was sent and whether the supplier replied.
        latest = max(own_interactions, key=lambda row: (row["date"], row["id"]), default=None)
        result.append({
            "supplier_id": sid,
            "company_name": supplier["company_name"],
            "legal_name": supplier["legal_name"],
            "inn": supplier["inn"],
            "domain": supplier["domain"],
            "website": supplier["website"],
            "primary_email": supplier["primary_email"],
            "primary_phone": supplier["primary_phone"],
            "last_contact_date": supplier["last_contact_date"],
            "contacts": [{key: row[key] for key in ("contact_id", "full_name", "position", "email", "phone")}
                         for row in own_contacts],
            "matched_categories": [{key: row[key] for key in
                                    ("category_l1", "category_l2", "category_l3", "category_l4",
                                     "source_mailbox", "source_message_id", "source_thread_id")}
                                   for row in category_hits],
            "matched_products": [{key: row[key] for key in
                                  ("brand", "manufacturer", "model", "article", "product_name",
                                   "source_mailbox", "source_message_id", "source_thread_id")}
                                 for row in product_hits],
            "matched_quotes": [{key: row[key] for key in
                                ("quote_id", "product_name", "price_unit", "price_total", "currency",
                                 "quote_date", "gmail_account", "message_id", "thread_id")}
                               for row in quote_hits],
            "thread_count": len({row["thread_id"] for row in own_interactions if row["thread_id"]}),
            "rfq_ids": sorted({row["rfq_id"] for row in own_interactions if row["rfq_id"]}),
            "outbound_requests": sum(row["direction"] == "OUT" for row in own_interactions),
            "inbound_responses": sum(row["direction"] == "IN" for row in own_interactions),
            "quote_count": len(own_quotes),
            "latest_interaction": (
                {key: latest[key] for key in ("gmail_account", "thread_id", "message_id", "date",
                                                "direction", "response_type", "subject")}
                if latest else None
            ),
        })
    return sorted(result, key=lambda row: (row["last_contact_date"], row["supplier_id"]), reverse=True)


def suggest_suppliers_for_rfq(
    db: sqlite3.Connection, *, query: str = "", category: str = "", product: str = "",
    brand: str = "", model: str = "", limit: int = 50,
) -> list[dict[str, Any]]:
    """Require at least one recorded product/category/quote fact for RFQ suggestions."""
    if limit < 1:
        return []
    terms = _tokens(" ".join(x for x in (query, category, product, brand, model) if x))
    if not terms:
        return []
    candidates = find_suppliers(db, " ".join(terms))
    return [row for row in candidates if row["matched_categories"] or row["matched_products"]
            or row["matched_quotes"]][:limit]
