"""Deterministic message, RFQ and conservative offer extraction."""

from __future__ import annotations

import re

from .signature_parser import extract_signature_text


_QUOTE_BOUNDARY = re.compile(
    r"^(?:-{2,}\s*(?:Original Message|Forwarded message)|"
    r"On\s+.{5,200}\s+wrote:|"
    r"(?:От|From):\s+.+@.+|"
    r"(?:В|On)\s+.{5,200}\s+(?:писал[аи]?:|wrote:))",
    re.IGNORECASE,
)
_TENDER_RE = re.compile(r"(?<![\w-])(T\d{2}-\d{4,6})(?![\w-])", re.I)
_TENDER_IN_COMPOSITE_RE = re.compile(r"(?<![\w-])(T\d{2}-\d{4,6})(?=-R\d{2,3}\b)", re.I)
_RFQ_RE = re.compile(r"(?<![\w-])(R\d{2,3})(?![\w-])", re.I)
_RFQ_IN_COMPOSITE_RE = re.compile(r"(?<![\w-])T\d{2}-\d{4,6}-(R\d{2,3})(?![\w-])", re.I)


def split_quoted(text: str | None) -> dict[str, str]:
    """Separate the author's message, quoted history and likely signature.

    The parser intentionally leaves quoted history available as context while
    callers should extract new facts primarily from ``new_message_body``.
    """
    if not text:
        return {"new_message_body": "", "quoted_history": "", "signature": ""}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)
    boundary = len(lines)
    for index, line in enumerate(lines):
        if line.lstrip().startswith(">") or _QUOTE_BOUNDARY.search(line.strip()):
            boundary = index
            break
    fresh = "".join(lines[:boundary]).strip()
    quoted = "".join(lines[boundary:]).strip()
    signature = extract_signature_text(fresh)
    if signature:
        index = fresh.rfind(signature)
        if index >= 0:
            fresh = fresh[:index].strip()
    return {
        "new_message_body": fresh,
        "quoted_history": quoted,
        "signature": signature,
    }


def extract_ids(subject: str | None, body: str | None = None) -> dict[str, str | None]:
    """Extract TENDER_ID and RFQ_ID; subject wins when both contain an ID."""
    tender_id: str | None = None
    rfq_id: str | None = None
    for text in (subject or "", body or ""):
        if not tender_id:
            match = _TENDER_IN_COMPOSITE_RE.search(text) or _TENDER_RE.search(text)
            if match:
                tender_id = match.group(1).upper()
        if not rfq_id:
            match = _RFQ_IN_COMPOSITE_RE.search(text) or _RFQ_RE.search(text)
            if match:
                rfq_id = match.group(1).upper()
    return {"tender_id": tender_id, "rfq_id": rfq_id}


_RESPONSE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NO_STOCK", re.compile(r"\b(?:нет\s+(?:в\s+)?наличи[яи]|отсутствует\s+на\s+склад|нет\s+нужной\s+модел[ьи]|out\s+of\s+stock)\b", re.I)),
    ("REFUSAL", re.compile(r"\b(?:отказываемся|не\s+сможем\s+(?:поставить|предложить|участвовать)|не\s+работаем\s+с|не\s+участвуем\s+в\s+тендерах|не\s+готовы\s+предложить|к\s+сожалению,?\s+предложить\s+не\s+можем)\b", re.I)),
    ("COMPANY_CARD_REQUEST", re.compile(r"(?:пришлите|направьте|просим\s+предоставить|необходима)\s+(?:пожалуйста\s+)?(?:карточк[уае]\s+(?:вашей\s+)?(?:организаци[ии]|предприятия|компании)|реквизиты)", re.I)),
    ("CLARIFICATION", re.compile(r"\b(?:уточните|уточните,?\s+пожалуйста|просим\s+уточнить|какое\s+количество|нужн[аы]\s+уточнени[яе])\b", re.I)),
    ("INVOICE", re.compile(r"\b(?:направляем|высылаем|во\s+вложении|прилагаем)\s+(?:вам\s+)?сч[её]т\b|\bсч[её]т\s+на\s+оплату\b", re.I)),
    ("CONTRACT", re.compile(r"\b(?:направляем|высылаем|во\s+вложении|прилагаем)\s+(?:вам\s+)?договор\b", re.I)),
    ("COMMERCIAL_OFFER", re.compile(r"\b(?:направля\w+|высыла\w+|прилага\w+|во\s+вложении|предлага\w+)\s+(?:вам\s+)?(?:коммерческ\w*\s+предложени\w*|кп)\b|\bкоммерческое\s+предложение\s+во\s+вложении\b", re.I)),
    ("PRICE", re.compile(r"\b(?:направля\w+|высыла\w+|прилага\w+|во\s+вложении)\s+(?:вам\s+)?прайс(?:-лист)?\b", re.I)),
    ("PRICE", re.compile(r"\b(?:цена|стоимость|прайс|прайс-лист)\b.{0,50}\b\d+[\d\s]*(?:₽|руб|р\.|usd|eur|\$|€)\b|\b\d[\d\s]*(?:₽|руб(?:\.|лей|ля)?|usd|eur|\$|€)", re.I)),
    ("DELIVERY_INFO", re.compile(r"\b(?:доставк[аиу]|самовывоз|транспортн\w+\s+компани\w+|срок\s+поставки)\b", re.I)),
    ("TECHNICAL_INFO", re.compile(r"\b(?:техническ\w+\s+(?:характеристик\w+|описани\w+)|паспорт\s+изделия|спецификация|datasheet)\b", re.I)),
    ("ACKNOWLEDGEMENT", re.compile(r"\b(?:запрос\s+получен|приняли\s+в\s+работу|получили\s+ваш\s+запрос|ответим\s+позже|благодарим\s+за\s+запрос)\b", re.I)),
)


def classify_response(text: str | None) -> str:
    """Classify only newly authored text; return OTHER for weak evidence."""
    fresh = split_quoted(text)["new_message_body"]
    for label, pattern in _RESPONSE_RULES:
        if pattern.search(fresh):
            return label
    return "OTHER"


_MONEY_RE = re.compile(
    r"(?<!\d)(?P<amount>\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?P<currency>₽|руб(?:\.|лей|ля)?|RUB|USD|EUR|\$|€)(?!\w)",
    re.I,
)
_QUANTITY_RE = re.compile(
    r"(?<!\d)(?P<quantity>\d+(?:[.,]\d+)?)\s*(?P<unit>шт\.?|штук[аи]?|ед\.?|компл\.?|упак\.?|кг|м2|м²|м)(?!\w)",
    re.I,
)
_TOTAL_LABEL_RE = re.compile(r"\b(?:итого|всего|сумма|общая\s+стоимость)\b", re.I)
_UNIT_LABEL_RE = re.compile(r"\b(?:цена\s+за\s+(?:ед\.?|шт\.?|единицу)|за\s+штуку|за\s+единицу|руб\.?/шт|₽/шт)\b", re.I)
_NON_PRODUCT_RE = re.compile(
    r"^(?:[\W_]*(?:проверяйте|подтвердите|добрый\s+день|всего\s+наименований|"
    r"партия\b|рц\s+стандарта|стоимость\s+за\s+шт|цена\s+за\s*$|"
    r"итого\b|всего\b|общая\s+стоимость\b|"
    r"есть\s+по\s+наличию|по\s+раз[ъь]ёмам|(?:free\s+)?(?:domestic\s+)?shipping\b)|"
    r"[\W_]*(?:возврат\s+каждой\s+позиции|доставка\s+по\s+городу))",
    re.I,
)


def _number(value: str) -> float:
    return float(value.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", "."))


def _currency(symbol: str) -> str:
    symbol = symbol.lower()
    return "RUB" if symbol.startswith("руб") or symbol in {"₽", "rub"} else "USD" if symbol in {"usd", "$"} else "EUR"


def extract_quote_lines(text: str | None) -> list[dict[str, object]]:
    """Extract explicit line items from text; ambiguous values remain ``None``.

    The parser does not derive prices, VAT, dates or stock from a category or
    market knowledge. It is intended for plain text/OCR output, not native XLS.
    """
    if not text:
        return []
    results: list[dict[str, object]] = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = " ".join(raw_line.split())
        if not line or len(line) > 300 or "&bull;" in line.lower() or "&nbsp;" in line.lower():
            continue
        prices = list(_MONEY_RE.finditer(line))
        if not prices or len({_currency(item.group("currency")) for item in prices}) != 1:
            continue
        product = line[: prices[0].start()].strip(" \t|;:-–—")
        product = re.sub(r"^\d+[.)]\s*", "", product)
        if _TOTAL_LABEL_RE.fullmatch(product) or not product:
            continue
        quantity_match = _QUANTITY_RE.search(product)
        quantity = _number(quantity_match.group("quantity")) if quantity_match else None
        unit = quantity_match.group("unit").rstrip(".").lower() if quantity_match else None
        if quantity_match:
            product = (product[: quantity_match.start()] + " " + product[quantity_match.end() :]).strip(" \t,|;:-–—×xх")
        product = re.sub(r"\b(?:цена\s+за\s+(?:ед\.?|шт\.?|единицу)?|цена)\s*[:=]?\s*$", "", product, flags=re.I).strip(" \t|;:-–—")
        product = re.sub(r"\s*[—–,-]\s*срок\s+поставки\b.*$", "", product, flags=re.I).strip(" \t|;:-–—")
        if (
            len(product) < 3
            or len(product) > 160
            or not re.search(r"[A-Za-zА-Яа-яЁё]{3}", product)
            or _TOTAL_LABEL_RE.fullmatch(product)
            or _NON_PRODUCT_RE.search(product)
            or product.startswith(("⚙", "⭑", "["))
            or re.match(r"^(?:ндс|доставка|предоплата|оплата|скидка|сч[её]т)\b", product, re.I)
        ):
            continue
        numbers = [_number(item.group("amount")) for item in prices]
        price_unit: float | None = None
        price_total: float | None = None
        unresolved_price: float | None = None
        if len(prices) >= 2:
            # Two money amounts alone do not establish a unit price and a
            # total: policies, subtotals and product specifications contain
            # them too. Require an explicit quantity and matching arithmetic.
            consistent = quantity is not None and quantity > 0 and len(prices) == 2 and abs(
                numbers[0] * quantity - numbers[1]
            ) <= max(1.0, numbers[1] * 0.01)
            labelled = bool(
                len(prices) == 2
                and _UNIT_LABEL_RE.search(line[: prices[0].start()])
                and _TOTAL_LABEL_RE.search(line[prices[0].end() : prices[1].start()])
            )
            if consistent or labelled:
                price_unit, price_total = numbers[0], numbers[1]
            else:
                unresolved_price = numbers[0]
        elif _UNIT_LABEL_RE.search(line) or re.search(r"(?:₽|руб\.?|RUB|USD|EUR|\$|€)\s*/\s*(?:шт|ед)", line, re.I):
            price_unit = numbers[0]
        elif _TOTAL_LABEL_RE.search(line[prices[0].end() :]):
            price_total = numbers[0]
        else:
            unresolved_price = numbers[0]
        vat_match = re.search(r"\bНДС\s*(\d{1,2})\s*%", line, re.I)
        without_vat = bool(re.search(r"\bбез\s+НДС\b", line, re.I))
        vat_included = True if re.search(r"\b(?:с|включая)\s+НДС\b", line, re.I) else False if without_vat else None
        discount = re.search(r"\bскидк\w*\s*(\d+(?:[.,]\d+)?)\s*%", line, re.I)
        results.append(
            {
                "product_name": product,
                "quantity": quantity,
                "unit": unit,
                "price_unit": price_unit,
                "price_total": price_total,
                "unresolved_price": unresolved_price,
                "currency": _currency(prices[0].group("currency")),
                "vat_rate": int(vat_match.group(1)) if vat_match else None,
                "vat_included": vat_included,
                "without_vat": without_vat if without_vat else None,
                "discount_percent": _number(discount.group(1)) if discount else None,
            }
        )
    return results
