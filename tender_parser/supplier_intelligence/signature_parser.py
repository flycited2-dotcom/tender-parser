"""Extract contact facts from the author's signature, without guessing gaps."""

from __future__ import annotations

import re

from .normalization import is_public_email_domain, normalize_email, normalize_phone


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
    r"\b(?:ООО|АО|ПАО|ЗАО|ОАО|ИП)\s+"
    r"(?:[«\"'„“][^»\"'”\n]{2,70}[»\"'”]|[^,;\n<>]{2,70})",
    re.IGNORECASE,
)
_POSITION_RE = re.compile(
    r"\b(?:менеджер(?:\s+отдела\s+продаж|\s+по\s+продажам)?|директор|руководител[ья]|специалист|инженер|"
    r"начальник|заместитель|отдел\s+продаж|sales\s+manager|account\s+manager)\b",
    re.IGNORECASE,
)
_CITY_RE = re.compile(r"\b(?:г\.|город)\s*([А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+)?)")
_EXT_RE = re.compile(r"\b(?:доб\.?|добавочн\w*|внутр\.?|ext\.?|extension)\s*[:№#]?\s*(\d{1,6})\b", re.I)
_TG_LABEL_RE = re.compile(r"\b(?:telegram|телеграм|tg|тг)\b", re.I)
_TG_URL_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})\b", re.I)
_TG_HANDLE_RE = re.compile(r"(?<!\w)@([A-Za-z][A-Za-z0-9_]{4,31})\b")
_TG_PLAIN_RE = re.compile(r"\b(?:telegram|телеграм|tg|тг)\s*[:：]\s*([A-Za-z][A-Za-z0-9_]{4,31})\b", re.I)
_WA_LABEL_RE = re.compile(r"\b(?:whats\s*app|w[.-]?app|вацап|ватсап)\b", re.I)
_WA_URL_RE = re.compile(r"(?:https?://)?wa\.me/(\d{10,15})\b", re.I)
_ADDRESS_RE = re.compile(
    r"^(?:(?:юридическ\w*|фактическ\w*|юр\.?|факт\.?)\s+)?адрес\s*[:：]\s*(.{8,180})$",
    re.I,
)
_STREET_ADDRESS_RE = re.compile(r"^(?:г\.|город)\s+.{2,130}\b(?:ул\.|улица|проспект|пер\.|переулок|д\.|дом)\b", re.I)
_REGION_RE = re.compile(r"^(?:регион|область|край|республика)\s*[:：]\s*(.{3,100})$", re.I)


def is_plausible_website(value: str | None) -> bool:
    """Accept a full hostname/URL only when it looks like a company site.

    A public mailbox or social network domain in a signature is contact context,
    not evidence of the supplier's own website.
    """
    candidate = str(value or "").strip()
    match = _SITE_RE.fullmatch(candidate)
    if not match:
        return False
    domain = match.group(1).casefold()
    return domain not in _NON_COMPANY_SITES and not is_public_email_domain(domain)


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
    if not any(_EMAIL_RE.search(line) or _PHONE_RE.search(line) or _TG_URL_RE.search(line)
               or _WA_URL_RE.search(line) for line in tail):
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
        "region": "",
        "address": "",
        "phone_ext": "",
        "telegram": "",
        "whatsapp": "",
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
        if not result["phone_ext"]:
            extension = _EXT_RE.search(line)
            if extension:
                result["phone_ext"] = extension.group(1)
        if not result["telegram"] and (_TG_LABEL_RE.search(line) or _TG_URL_RE.search(line)):
            handle = _TG_URL_RE.search(line) or _TG_HANDLE_RE.search(line) or _TG_PLAIN_RE.search(line)
            if handle:
                result["telegram"] = "@" + handle.group(1)
            elif _TG_LABEL_RE.search(line):
                tagged_phone = _PHONE_RE.search(line)
                if tagged_phone:
                    result["telegram"] = normalize_phone(tagged_phone.group())
        if not result["whatsapp"] and (_WA_LABEL_RE.search(line) or _WA_URL_RE.search(line)):
            wa_link = _WA_URL_RE.search(line)
            wa_phone = _PHONE_RE.search(line)
            if wa_link or wa_phone:
                result["whatsapp"] = normalize_phone((wa_link or wa_phone).group(1) if wa_link else wa_phone.group())
        if not result["address"]:
            address = _ADDRESS_RE.search(line)
            if address:
                result["address"] = address.group(1).strip(" ,;.")
            elif _STREET_ADDRESS_RE.search(line):
                result["address"] = line.strip(" ,;.")
        if not result["region"]:
            region = _REGION_RE.search(line)
            if region:
                result["region"] = region.group(1).strip(" ,;.")
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
                if is_plausible_website(domain):
                    result["website"] = domain
                    break
    result["emails"] = emails
    result["phones"] = phones
    if not phones:
        result["phone_ext"] = ""
    return result
