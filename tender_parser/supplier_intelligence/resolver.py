"""Deterministic supplier identity matching.

This module does not create suppliers.  The store applies the result inside the
same transaction that records the source message, so a retry cannot create a
second company.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .normalization import (
    PUBLIC_EMAIL_DOMAINS as PUBLIC_DOMAINS,
    is_public_email_domain,
    normalize_company,
    normalize_email,
    normalize_phone,
)

OWN_EMAILS = frozenset({"termoark@gmail.com", "flycited@gmail.com"})
OWN_DOMAINS = frozenset({"simfer.com.ru"})


def normalize_domain(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    if "@" in text and "://" not in text:
        text = text.rsplit("@", 1)[-1]
    else:
        parsed = urlsplit(text if "://" in text else "//" + text)
        text = parsed.hostname or ""
    text = text.removeprefix("www.").strip(".")
    labels = text.split(".")
    if len(labels) < 2 or len(labels[-1]) < 2:
        return ""
    return text


def is_public_domain(value: object) -> bool:
    return is_public_email_domain(normalize_domain(value)) or normalize_domain(value) in {
        "yandex.com", "proton.me", "protonmail.com",
    }


def normalize_inn(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) in (10, 12) else ""


@dataclass(frozen=True)
class Identity:
    inn: str = ""
    domain: str = ""
    website_domain: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""


@dataclass(frozen=True)
class Candidate:
    supplier_id: str
    confidence: float
    reasons: tuple[str, ...]


def identity_from_message(message: dict) -> Identity:
    supplier = message.get("supplier") or {}
    signature = message.get("signature") or {}
    if not isinstance(supplier, dict):
        supplier = {}
    if not isinstance(signature, dict):
        signature = {}
    direction = str(message.get("direction") or "").upper()
    mailbox = normalize_email(message.get("gmail_account"))
    candidates = (
        [message.get("from_email"), message.get("reply_to")]
        if direction == "IN"
        else [*(message.get("to_emails") or []), *(message.get("cc_emails") or [])]
    )
    emails = [
        normalize_email(supplier.get("email")),
        *[normalize_email(x) for x in (supplier.get("emails") or [])],
        *[normalize_email(x) for x in candidates],
        *[normalize_email(x) for x in (signature.get("emails") or [])],
    ]
    email = next(
        (e for e in emails if e and e != mailbox and e not in OWN_EMAILS and normalize_domain(e) not in OWN_DOMAINS),
        "",
    )
    website = supplier.get("website") or signature.get("website") or ""
    company = (
        supplier.get("company_name") or supplier.get("name")
        or supplier.get("legal_name") or signature.get("company") or ""
    )
    phones = supplier.get("phones") or signature.get("phones") or []
    phone = supplier.get("phone") or (phones[0] if phones else "")
    domain = normalize_domain(supplier.get("domain") or email)
    return Identity(
        inn=normalize_inn(supplier.get("inn")),
        domain="" if is_public_domain(domain) or domain in OWN_DOMAINS else domain,
        website_domain=normalize_domain(website),
        email=email,
        phone=normalize_phone(phone),
        company=normalize_company(company),
    )


def match_candidates(identity: Identity, suppliers: list[dict], aliases: list[dict]) -> list[Candidate]:
    by_supplier: dict[str, dict[str, set[str]]] = {}
    for row in suppliers:
        supplier_id = str(row["supplier_id"])
        values = by_supplier.setdefault(supplier_id, {k: set() for k in ("INN", "DOMAIN", "WEBSITE", "EMAIL", "PHONE", "COMPANY_NAME")})
        for key, value, normalizer in (
            ("INN", row.get("inn"), normalize_inn),
            ("DOMAIN", row.get("domain"), normalize_domain),
            ("WEBSITE", row.get("website"), normalize_domain),
            ("EMAIL", row.get("primary_email"), normalize_email),
            ("PHONE", row.get("primary_phone"), normalize_phone),
            ("COMPANY_NAME", row.get("company_name"), normalize_company),
            ("COMPANY_NAME", row.get("legal_name"), normalize_company),
        ):
            normalized = normalizer(value)
            if normalized:
                values[key].add(normalized)
    for row in aliases:
        supplier_id = str(row["supplier_id"])
        if supplier_id not in by_supplier:
            continue
        key = str(row.get("alias_type") or "").upper()
        if key in by_supplier[supplier_id] and row.get("normalized_value"):
            by_supplier[supplier_id][key].add(str(row["normalized_value"]))

    results: list[Candidate] = []
    for supplier_id, values in by_supplier.items():
        inn = bool(identity.inn and identity.inn in values["INN"])
        domain = bool(identity.domain and identity.domain in values["DOMAIN"] and not is_public_domain(identity.domain))
        website = bool(identity.website_domain and identity.website_domain in values["WEBSITE"])
        email = bool(identity.email and identity.email in values["EMAIL"])
        phone = bool(identity.phone and identity.phone in values["PHONE"])
        company = bool(identity.company and identity.company in values["COMPANY_NAME"])
        # A shared phone, buyer tax number or company mention in a forwarded
        # thread cannot establish that two unrelated sender domains are one
        # supplier. Cross-domain identities need an exact known email or a
        # separate manual review before their facts can be combined.
        known_domains = {value for value in values["DOMAIN"] if not is_public_domain(value)}
        if identity.domain and known_domains and identity.domain not in known_domains and not email:
            continue
        reasons = tuple(name for name, matched in (("inn", inn), ("domain", domain), ("website", website), ("email", email), ("phone", phone), ("company", company)) if matched)
        if not reasons:
            continue
        if inn:
            score = 1.0
        elif domain and company:
            score = 0.95
        elif email and company:
            score = 0.93
        elif phone and company:
            score = 0.90
        elif website and company:
            score = 0.80
        elif domain and email:
            score = 0.90
        elif email:
            score = 0.85
        elif domain:
            score = 0.85
        elif website:
            score = 0.80
        elif phone:
            score = 0.75
        else:
            score = 0.70
        results.append(Candidate(supplier_id, score, reasons))
    return sorted(results, key=lambda item: (-item.confidence, item.supplier_id))
