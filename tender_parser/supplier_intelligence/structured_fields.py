"""Conservative business facts that require explicit first-party evidence."""

from __future__ import annotations

import re


_REFUSAL_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("Нет нужной модели", "NO_STOCK", re.compile(r"\b(?:нет\s+нужной\s+модел[ьи]|нужн\w+\s+модел\w+\s+(?:нет|отсутствует)|модель\s+снят\w+\s+с\s+производства)\b", re.I)),
    ("Нет необходимого количества", "REFUSAL", re.compile(r"\b(?:нет\s+(?:нужного|необходимого|такого)\s+количества|не\s+можем\s+поставить\s+(?:в\s+)?таком\s+количестве|нет\s+возможности\s+поставки\s+в\s+таком\s+количестве)\b", re.I)),
    ("Не работаем с регионом", "REFUSAL", re.compile(r"\b(?:не\s+(?:работаем|поставляем|доставляем|отгружаем)\s+(?:в|по|с)\s+(?:этим\s+)?(?:регион\w+|крым\w*|севастопол\w*|симферопол\w*)|в\s+(?:этот\s+)?регион\w+\s+не\s+(?:поставляем|доставляем))\b", re.I)),
    ("Не участвуем в тендерах", "REFUSAL", re.compile(r"\b(?:не\s+участвуем\s+в\s+(?:тендерах|закупках)|с\s+тендерами\s+не\s+работаем)\b", re.I)),
    ("Нет производства", "REFUSAL", re.compile(r"\b(?:не\s+(?:производим|выпускаем)|производство\s+(?:прекращено|остановлено)|снят\w+\s+с\s+производства)\b", re.I)),
    ("Проект зарегистрирован на другого дилера", "REFUSAL", re.compile(r"\b(?:проект|объект)\s+(?:уже\s+)?зарегистрирован\w*\s+на\s+(?:другого|иного)\s+дилер\w*\b", re.I)),
    ("Слишком короткий срок", "REFUSAL", re.compile(r"\b(?:слишком\s+коротк\w+\s+срок\w*|не\s+успе\w+\s+(?:к|в)\s+(?:указанн\w+|эт\w+)\s+срок\w*|срок\w*\s+(?:не\s+позволя\w+|слишком\s+коротк\w*))\b", re.I)),
    ("Нет товара", "NO_STOCK", re.compile(r"\b(?:нет\s+(?:товара|в\s+наличии)|отсутствует\s+(?:на\s+складе|в\s+наличии)|товар\s+закончился)\b", re.I)),
    ("Не можем предоставить КП", "REFUSAL", re.compile(r"\b(?:кп|коммерческ\w+\s+предложени\w+)\s+(?:предоставить|направить|подготовить)\s+не\s+можем\b|\bне\s+можем\s+(?:предоставить|направить|подготовить)\s+(?:кп|коммерческ\w+\s+предложени\w+)\b", re.I)),
    ("Не поставляем", "REFUSAL", re.compile(r"\bне\s+(?:поставляем|отгружаем)\b", re.I)),
)

_ROLE_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OFFICIAL_DISTRIBUTOR", re.compile(r"\bофициальн\w*\s+дистрибьютор\w*\b", re.I)),
    ("MANUFACTURER", re.compile(r"\bпроизводител\w*\b|\bсобственн\w*\s+производств\w*\b", re.I)),
    ("DEALER", re.compile(r"\b(?:официальн\w*\s+)?дилер\w*\b", re.I)),
    ("RESELLER", re.compile(r"\b(?:реселлер\w*|перепродавц\w*)\b", re.I)),
)
_SELF_CLAIM = re.compile(
    r"\b(?:мы|наша\s+компания|наша\s+организация|наше\s+предприятие)\b"
    r"|\bявляемся\b|\bу\s+нас\s+собственн\w*\s+производств\w*\b",
    re.I,
)
_ROLE_SIGNATURE = re.compile(
    r"^(?:(?:ООО|АО|ПАО|ОАО|ЗАО|ИП)\s+.{2,90}?[,;:—-]\s*)?"
    r"(?:официальн\w*\s+)?(?:производител\w*|дистрибьютор\w*|дилер\w*|реселлер\w*)\b",
    re.I,
)
_RFQ_REQUEST = re.compile(
    r"\b(?:rfq|запрос\s+(?:цен[ы]?|коммерческ\w*\s+предложени\w*|кп)|"
    r"просим\s+(?:предоставить|направить|сообщить|выслать|подготовить)\s+"
    r"(?:нам\s+)?(?:цен[ыу]|стоимост\w*|кп|коммерческ\w*\s+предложени\w*|наличи\w*)|"
    r"интересует\s+(?:цен[аыу]|стоимост\w*|наличи\w*))\b",
    re.I,
)


def refusal_reason(fresh_body: str) -> tuple[str, str]:
    """Return a dated interaction reason only for an explicit negative claim."""
    for reason, response_type, pattern in _REFUSAL_RULES:
        if pattern.search(fresh_body):
            return reason, response_type
    return "", ""


def supplier_role(fresh_body: str, signature: str) -> dict[str, str]:
    """Trust self-descriptions and supplier signatures, not third-party mentions."""
    roles: set[str] = set()
    for line in fresh_body.splitlines():
        line = line.strip()
        if len(line) > 200 or not _SELF_CLAIM.search(line):
            continue
        roles.update(role for role, pattern in _ROLE_WORDS if pattern.search(line))
    for line in signature.splitlines():
        line = line.strip()
        if len(line) > 140 or not (_SELF_CLAIM.search(line) or _ROLE_SIGNATURE.search(line)):
            continue
        roles.update(role for role, pattern in _ROLE_WORDS if pattern.search(line))
    selected = next((role for role in ("MANUFACTURER", "OFFICIAL_DISTRIBUTOR", "DEALER", "RESELLER") if role in roles), "")
    return {
        "supplier_type": selected,
        "manufacturer": "Да" if "MANUFACTURER" in roles else "",
        "distributor": "Да" if "OFFICIAL_DISTRIBUTOR" in roles else "",
        "dealer": "Да" if "DEALER" in roles else "",
    }


def message_type(direction: str, subject: str, fresh_body: str, response_type: str) -> str:
    """Separate an explicit RFQ from ordinary outgoing and incoming mail."""
    if direction == "OUT":
        return "RFQ" if _RFQ_REQUEST.search(subject + "\n" + fresh_body) else "OUTBOUND"
    return response_type if response_type and response_type != "OTHER" else "INBOUND"
