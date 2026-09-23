"""Extract contact facts from the author's signature, without guessing gaps."""

from __future__ import annotations

import re

from .normalization import normalize_email, normalize_phone


_SIGNOFF_RE = re.compile(
    r"^(?:с\s+уважением|с\s+наилучшими\s+пожеланиями|best\s+regards|"
    r"kind\s+regards|regards|respectfully|--\s*$)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+%-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}", re.UNICODE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+7|8)[\s().-]*(?:\d[\s().-]*){10}(?!\d)")
_SITE_RE = re.compile(
    r"(?<![@\w])(?:https?://)?(?:www\.)?"
    r"((?:[a-zа-яё0-9](?:[a-zа-яё0-9-]{0,61}[a-zа-яё0-9])?\.)+"
    r"(?:[a-z]{2,24}|рф|рус|москва|онлайн|сайт|орг|дети|бел))"
    r"(?:/[^\s]*)?(?![@\w.-])",
    re.IGNORECASE,
)
_NON_COMPANY_SITES = {"vk.com", "t.me", "wa.me", "facebook.com", "instagram.com", "youtu.be"}
_PERSON_RE = re.compile(r"^[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){1,2}$")
_COMPANY_RE = re.compile(
    r"\b(?:ООО|АО|ПАО|ЗАО|ОАО|ИП)\s+(?:[«\"']?[^,;\n<>]{2,70}[»\"']?)",
    re.IGNORECASE,
)
_POSITION_RE = re.compile(
    r"\b(?:менеджер(?:\s+отдела\s+продаж|\s+по\s+продажам)?|директор|руководител[ья]|специалист|инженер|"
    r"начальник|заместитель|отдел\s+продаж|sales\s+manager|account\s+manager)\b",
    re.IGNORECASE,
)
_CITY_RE = re.compile(r"\b(?:г\.|город)\s*([А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+)?)")


def extract_signature_text(text: str | None) -> str:
    """Return a likely signature block from the fresh part of a message."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    # Never take a sign-off out of a previous, quoted letter.
    for index, line in enumerate(lines):
        if (
            line.lstrip().startswith(">")
            or re.match(r"^-{2,}\s*Original Message", line, re.I)
            or re.match(r"^On\s+.{5,200}\s+wrote:", line, re.I)
        ):
            lines = lines[:index]
            break
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    for index in range(len(lines) - 1, max(-1, len(lines) - 16), -1):
        if _SIGNOFF_RE.search(lines[index].strip()):
            return "\n".join(lines[index:]).strip()
    tail = lines[-12:]
    if not any(_EMAIL_RE.search(line) or _PHONE_RE.search(line) for line in tail):
        return ""
    # A short tail with a name/company/position and a contact is a signature.
    for index, line in enumerate(tail):
        clean = line.strip(" ,;|")
        if _PERSON_RE.fullmatch(clean) or _COMPANY_RE.search(clean) or _POSITION_RE.search(clean):
            return "\n".join(tail[index:]).strip()
    return ""


def parse_signature(text: str | None) -> dict[str, object]:
    """Parse the author's signature into the stable schema from the brief."""
    result: dict[str, object] = {
        "person": "",
        "position": "",
        "company": "",
        "phones": [],
        "emails": [],
        "website": "",
        "city": "",
    }
    signature = extract_signature_text(text)
    if not signature:
        return result
    lines = [line.strip(" ,;|") for line in signature.splitlines() if line.strip()]
    emails: list[str] = []
    phones: list[str] = []
    for line in lines:
        for candidate in _EMAIL_RE.findall(line):
            email = normalize_email(candidate)
            if email and email not in emails:
                emails.append(email)
        for match in _PHONE_RE.finditer(line):
            phone = normalize_phone(match.group())
            if phone and phone not in phones:
                phones.append(phone)
        if not result["company"]:
            company = _COMPANY_RE.search(line)
            if company:
                result["company"] = _PHONE_RE.split(company.group(), maxsplit=1)[0].strip(" ,;|*\u00a0")
        position = _POSITION_RE.search(line)
        if not result["position"] and position:
            result["position"] = position.group().strip()
        if not result["person"] and _PERSON_RE.fullmatch(line):
            result["person"] = line
        if not result["city"]:
            city = _CITY_RE.search(line)
            if city:
                result["city"] = city.group(1)
        if not result["website"]:
            site_line = _EMAIL_RE.sub(" ", _PHONE_RE.sub(" ", line))
            for site in _SITE_RE.finditer(site_line):
                domain = site.group(1).lower()
                if domain not in _NON_COMPANY_SITES:
                    result["website"] = domain
                    break
    result["emails"] = emails
    result["phones"] = phones
    return result
