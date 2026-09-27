"""Turn Gmail API messages into auditable supplier facts."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Callable

from tender_parser.supplier_intelligence.attachment_parser import (
    extract_attachment_text,
    extract_spreadsheet_quote_lines,
)
from tender_parser.supplier_intelligence.extraction import (
    classify_response,
    extract_ids,
    extract_quote_lines,
    split_quoted,
)
from tender_parser.supplier_intelligence.normalization import is_public_email_domain, normalize_email
from tender_parser.supplier_intelligence.product_classifier import classify_products
from tender_parser.supplier_intelligence.signature_parser import is_plausible_website, parse_signature
from tender_parser.supplier_intelligence.structured_fields import (
    message_type as classify_message_type,
    refusal_reason,
    supplier_role,
)


_INN = re.compile(r"\bИНН\s*[:№]?\s*(\d{10}|\d{12})\b", re.I)
_KPP = re.compile(r"\bКПП\s*[:№]?\s*(\d{9})\b", re.I)
_OGRN = re.compile(r"\bОГРН\s*[:№]?\s*(\d{13}|\d{15})\b", re.I)
_LEGAL_NAME = re.compile(
    r"\b(?:ООО|АО|ПАО|ОАО|ЗАО|ИП)\s*"
    r"(?:[«\"'„“][^»\"'”\n]{2,70}[»\"'”]|[^\n,;:]{3,70})",
    re.I,
)
_SELF_IDENTITY = re.compile(
    r"^\s*(?:мы\b|наша\s+(?:компания|организация)\b|наш(?:и|\s+инн|\s+кпп|\s+огрн)\b|"
    r"реквизиты\s+нашей\s+(?:компании|организации)\b)",
    re.I,
)
_OUR_REQUISITES = re.compile(r"^\s*(?:наши\s+реквизиты|реквизиты\s+нашей\s+(?:компании|организации))\b", re.I)
_BUSINESS = re.compile(
    r"\b(?:кп|коммерческ\w* предложени\w*|сч[её]т|прайс|цен[аыу]|"
    r"стоимост\w*|наличи\w*|поставк\w*|запрос\w*|rfq|спецификац\w*)\b",
    re.I,
)
_IGNORE_SUBJECT = re.compile(
    r"(?:авторизац|код\s+(?:для\s+)?(?:входа|подтверждени[яю])|"
    r"подтверждени[ея]\s+(?:аккаунта|входа)|восстановлени[ея] парол|"
    r"security alert|уведомление о входе|новый вход|delivery status notification)",
    re.I,
)
_AUTOMATED_LOCAL = re.compile(r"^(?:no[._-]?reply|do[._-]?not[._-]?reply|mailer-daemon|postmaster|notifications?)$", re.I)
_SYSTEM_DOMAIN_SUFFIXES = (
    "google.com", "googlemail.com", "avito.ru", "ozon.ru", "yandex.net",
    "gov.ru", "gosuslugi.ru",
)
_SUPPORT_SUFFIXES = {".pdf", ".xls", ".xlsx", ".doc", ".docx"}
_FORWARD_MARKER = re.compile(
    r"(?im)^-{2,}\s*(?:Forwarded message|Пересланное сообщение|Пересылаемое сообщение)\s*-*\s*$"
)


def normalize_gmail_message(
    account: str,
    raw: dict,
    *,
    own_emails: frozenset[str] | set[str],
    own_domains: frozenset[str] | set[str],
    ignored_sender_domains: frozenset[str] | set[str] = frozenset(),
    ignored_sender_prefixes: frozenset[str] | set[str] = frozenset(),
    attachment_loader: Callable[[str, str], dict] | None = None,
    attachment_max_bytes: int = 20 * 1024 * 1024,
) -> dict:
    """No network work except the injected attachment loader; never sends mail."""
    message_id = str(raw.get("id") or "")
    thread_id = str(raw.get("threadId") or "")
    if not message_id or not thread_id:
        raise ValueError("Gmail message has no id or threadId")
    headers = _headers(raw.get("payload") or {})
    sender = _addresses(headers.get("from", ""))
    recipients = _addresses(headers.get("to", ""))
    carbon_copy = _addresses(headers.get("cc", ""))
    reply_to = _addresses(headers.get("reply-to", ""))
    from_email = sender[0][1] if sender else ""
    own = {value.casefold() for value in own_emails}
    own_domain_set = {value.casefold() for value in own_domains}
    direction = "OUT" if from_email in own or _domain(from_email) in own_domain_set else "IN"
    outer_subject = headers.get("subject", "").strip()
    subject = outer_subject
    text_parts, html_parts, attachment_parts = _parts(raw.get("payload") or {})
    body = "\n".join(text_parts).strip() or _html_to_text("\n".join(html_parts))
    forwarded = _forwarded_message(body)
    if forwarded and forwarded["email"]:
        # The enclosing forward is transport; the embedded message is the
        # supplier interaction. Parse its own body/signature and keep provenance.
        from_email = forwarded["email"]
        sender = [(forwarded["name"], from_email)]
        direction = "OUT" if from_email in own or _domain(from_email) in own_domain_set else "IN"
        subject = forwarded["subject"] or outer_subject
        body = forwarded["body"]
        if forwarded["to"]:
            recipients = _addresses(forwarded["to"])
        reply_to = _addresses(forwarded["reply_to"])
    split = split_quoted(body)
    if isinstance(split, dict):
        new_body = str(split.get("new_message_body") or "")
        quoted = str(split.get("quoted_history") or "")
        signature_text = str(split.get("signature") or "")
    else:
        new_body, quoted, signature_text = tuple(split)
    signature = parse_signature(signature_text or new_body) if direction == "IN" else {}
    if not isinstance(signature, dict):
        signature = {}
    # A copied customer signature can trail a supplier reply without a standard
    # quote marker. Never attribute our own contact block to that supplier.
    if direction == "IN" and signature_text and any(
        own_address in signature_text.casefold() for own_address in own
    ):
        signature = {}
        signature_text = ""

    target_addresses = [entry[1] for entry in (recipients + carbon_copy)] if direction == "OUT" else []
    if direction == "IN":
        target_addresses = [entry[1] for entry in (reply_to or sender)]
        if forwarded:
            target_addresses.insert(0, forwarded["email"])
    target_addresses = [
        value for value in dict.fromkeys(target_addresses)
        if value and value not in own and _domain(value) not in own_domain_set
    ]
    partner_email = target_addresses[0] if target_addresses else ""
    display = ""
    if direction == "IN" and sender:
        display = sender[0][0]
    elif direction == "OUT":
        display = next((name for name, value in recipients + carbon_copy if value == partner_email), "")
    details = "\n".join([subject, new_body, signature_text])
    identity_details = _supplier_identity_text(new_body, signature_text) if direction == "IN" else ""
    legal_match = _LEGAL_NAME.search(identity_details) if direction == "IN" else None
    legal_name = legal_match.group(0).strip(" ,.;:") if legal_match else ""
    company_name = str(signature.get("company") or legal_name or "").strip() if direction == "IN" else ""
    if not company_name and display and not _looks_like_person(display) and not _looks_like_service_name(display):
        company_name = display
    website = str(signature.get("website") or "").strip() if direction == "IN" else ""
    if website and not is_plausible_website(website):
        website = ""
    domain = _domain(partner_email)
    if website:
        site_domain = re.sub(r"^https?://", "", website, flags=re.I).split("/", 1)[0].removeprefix("www.")
        domain = site_domain or domain
    role = supplier_role(new_body, signature_text) if direction == "IN" else {}
    supplier = {
        "company_name": company_name,
        "legal_name": legal_name,
        "inn": _first(_INN, identity_details) if direction == "IN" else "",
        "kpp": _first(_KPP, identity_details) if direction == "IN" else "",
        "ogrn": _first(_OGRN, identity_details) if direction == "IN" else "",
        "website": website,
        "domain": domain,
        "email": partner_email,
        "phone": next(iter(signature.get("phones") or []), ""),
        "city": str(signature.get("city") or ""),
        "region": str(signature.get("region") or ""),
        "address": str(signature.get("address") or ""),
        "contact": {
            "phone_ext": str(signature.get("phone_ext") or ""),
            "telegram": str(signature.get("telegram") or ""),
            "whatsapp": str(signature.get("whatsapp") or ""),
        } if direction == "IN" else {},
        **role,
    }

    attachments: list[dict] = []
    attachment_texts: list[tuple[str, str]] = []
    attachment_quotes: list[dict] = []
    for part in attachment_parts:
        filename = str(part.get("filename") or "")
        data = part.get("body") or {}
        attachment_id = str(data.get("attachmentId") or "")
        size = int(data.get("size") or 0)
        entry = {
            "message_id": message_id,
            "attachment_id": attachment_id,
            "filename": filename,
            "mime_type": str(part.get("mimeType") or ""),
            "file_size": size,
        }
        attachments.append(entry)
        if Path(filename).suffix.casefold() not in _SUPPORT_SUFFIXES or size > attachment_max_bytes:
            continue
        try:
            payload = data if data.get("data") else (attachment_loader(message_id, attachment_id) if attachment_loader and attachment_id else {})
            raw_data = _decode(str(payload.get("data") or ""))
            parsed_text = extract_attachment_text(filename, raw_data) if raw_data else ""
            if parsed_text:
                attachment_texts.append((filename, parsed_text[:250_000]))
            if direction == "IN" and Path(filename).suffix.casefold() in {".xlsx", ".xls"}:
                attachment_quotes.extend({
                    **line,
                    "attachment_filename": filename,
                    "attachment_type": entry["mime_type"],
                } for line in extract_spreadsheet_quote_lines(filename, raw_data))
        except Exception as exc:
            entry["parse_error"] = str(exc)[:300]

    context_text = "\n".join([subject, new_body, *[value for _, value in attachment_texts]])
    ids = extract_ids(subject, new_body + "\n" + quoted[:5000])
    if not isinstance(ids, dict):
        ids = {"tender_id": "", "rfq_id": ""}
    categories_products = classify_products(subject, context_text, [entry["filename"] for entry in attachments])
    products = categories_products if isinstance(categories_products, list) else []
    categories = _unique_categories(products)
    quote_lines = extract_quote_lines(new_body) if direction == "IN" else []
    if not isinstance(quote_lines, list):
        quote_lines = []
    for filename, value in (attachment_texts if direction == "IN" else []):
        if Path(filename).suffix.casefold() in {".xlsx", ".xls"}:
            continue
        for line in extract_quote_lines(value) or []:
            if isinstance(line, dict):
                quote_lines.append({**line, "attachment_filename": filename})
    quote_lines.extend(attachment_quotes)
    response_type = classify_response(new_body + "\n" + " ".join(entry["filename"] for entry in attachments)) if direction == "IN" else ""
    reason, reason_response = refusal_reason(new_body) if direction == "IN" else ("", "")
    if reason_response:
        response_type = reason_response
    signal = bool(products or quote_lines or _BUSINESS.search(details) or company_name)
    if _IGNORE_SUBJECT.search(subject):
        signal = False
    partner_domain = _domain(partner_email)
    ignored_domains = (*_SYSTEM_DOMAIN_SUFFIXES, *ignored_sender_domains)
    if any(partner_domain == suffix or partner_domain.endswith("." + suffix)
           for suffix in ignored_domains if suffix):
        signal = False
    local_part = partner_email.split("@", 1)[0] if "@" in partner_email else ""
    automated_local = _AUTOMATED_LOCAL.fullmatch(local_part) or any(
        local_part.startswith(prefix) for prefix in ignored_sender_prefixes if prefix
    )
    if automated_local and not (
        products or quote_lines or _BUSINESS.search(new_body)
    ):
        signal = False
    if not partner_email:
        signal = False
    forwarded_timestamp = _forwarded_date(forwarded["date"]) if forwarded else ""
    return {
        "gmail_account": account,
        "message_id": message_id,
        "thread_id": thread_id,
        "history_id": str(raw.get("historyId") or ""),
        "date": forwarded_timestamp or _message_date(raw, headers),
        "direction": direction,
        "from_email": from_email,
        "to_emails": [value for _, value in recipients],
        "cc_emails": [value for _, value in carbon_copy],
        "reply_to": reply_to[0][1] if reply_to else "",
        "subject": subject,
        "new_message_body": new_body,
        "quoted_history": quoted,
        "signature": signature,
        "supplier": supplier,
        "supplier_email": partner_email,
        "tender_id": str(ids.get("tender_id") or ""),
        "rfq_id": str(ids.get("rfq_id") or ""),
        "categories": categories,
        "products": products,
        "response_type": response_type,
        "message_type": classify_message_type(direction, subject, new_body, response_type),
        "refusal_reason": reason,
        "quote_lines": quote_lines,
        "attachments": attachments,
        "supplier_signal": signal,
        "forwarded_original_subject": forwarded["subject"] if forwarded else "",
        "forwarded_original_date": forwarded["date"] if forwarded else "",
        "outer_subject": outer_subject if forwarded else "",
    }


def _headers(payload: dict) -> dict[str, str]:
    return {str(item.get("name") or "").casefold(): str(item.get("value") or "") for item in payload.get("headers", [])}


def _addresses(value: str) -> list[tuple[str, str]]:
    return [(name.strip(), normalize_email(email)) for name, email in getaddresses([value]) if normalize_email(email)]


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)) if value else b""


def _parts(root: dict) -> tuple[list[str], list[str], list[dict]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []

    def visit(part: dict) -> None:
        filename = str(part.get("filename") or "")
        kind = str(part.get("mimeType") or "").casefold()
        body = part.get("body") or {}
        if filename:
            attachments.append(part)
        elif body.get("data") and kind == "text/plain":
            text_parts.append(_decode(str(body["data"])).decode("utf-8", "replace"))
        elif body.get("data") and kind == "text/html":
            html_parts.append(_decode(str(body["data"])).decode("utf-8", "replace"))
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                visit(child)

    visit(root)
    return text_parts, html_parts, attachments


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)
    except ImportError:
        return unescape(re.sub(r"<[^>]+>", " ", value))


def _first(pattern: re.Pattern[str], value: str) -> str:
    match = pattern.search(value)
    return match.group(1) if match else ""


def _supplier_identity_text(new_body: str, signature_text: str) -> str:
    """Read company identifiers from sender-owned text only.

    Supplier replies often quote our legal details or ask for our company
    card. Those identifiers must not become aliases of the sender.
    """
    lines = [signature_text]
    for line in new_body.splitlines():
        if _SELF_IDENTITY.search(line) or _OUR_REQUISITES.search(line):
            lines.append(line[:300])
    return "\n".join(lines)


def _domain(value: str) -> str:
    return value.rsplit("@", 1)[-1].casefold() if "@" in value else ""


def _looks_like_person(value: str) -> bool:
    words = value.split()
    if words and words[0].casefold() in {"ооо", "ао", "пао", "оао", "зао", "ип"}:
        return False
    return len(words) in {2, 3} and all(word[:1].isupper() for word in words)


def _looks_like_service_name(value: str) -> bool:
    """Do not turn public mail or invalid domain labels into company names."""
    text = value.strip()
    if re.fullmatch(r"(?:менеджер|отдел\s+продаж|специалист|директор|sales\s+manager)", text, re.I):
        return True
    if "@" in text or is_public_email_domain(text):
        return True
    return bool("." in text and not any(ch.isspace() for ch in text) and not is_plausible_website(text))


def _forwarded_message(body: str) -> dict[str, str] | None:
    """Parse the embedded RFC-like block of a Gmail forward, if present."""
    marker = _FORWARD_MARKER.search(body[:5000])
    if not marker:
        return None
    remaining = body[marker.end():].lstrip("\r\n")
    lines = remaining.splitlines(keepends=True)
    keys = {
        "from": "from", "от": "from", "date": "date", "дата": "date",
        "subject": "subject", "тема": "subject", "to": "to", "кому": "to",
        "reply-to": "reply_to", "ответить": "reply_to",
    }
    fields = {"from": "", "date": "", "subject": "", "to": "", "reply_to": ""}
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            if fields["from"]:
                break
            continue
        match = re.match(r"^([A-Za-zА-Яа-яЁё-]+):\s*(.*)$", line)
        if not match or match.group(1).casefold() not in keys:
            break
        fields[keys[match.group(1).casefold()]] = match.group(2).strip()
        index += 1
    addresses = _addresses(fields["from"])
    if not addresses:
        return None
    name, email = addresses[0]
    return {
        "name": name, "email": email, "subject": fields["subject"],
        "date": fields["date"], "to": fields["to"], "reply_to": fields["reply_to"],
        "body": "".join(lines[index:]).strip(),
    }


def _forwarded_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def _message_date(raw: dict, headers: dict[str, str]) -> str:
    try:
        timestamp = int(raw.get("internalDate") or 0) / 1000
        if timestamp:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        value = parsedate_to_datetime(headers.get("date", ""))
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    except (TypeError, ValueError, IndexError):
        return datetime.now(timezone.utc).isoformat()


def _unique_categories(products: list[dict]) -> list[dict]:
    values: dict[tuple[str, str, str, str], dict] = {}
    for product in products:
        category = {key: product.get(key, "") for key in ("category_l1", "category_l2", "category_l3", "category_l4")}
        key = tuple(str(category[field]) for field in category)
        if key[0]:
            values[key] = {**category, "confidence": product.get("confidence", 0.7)}
    return list(values.values())
