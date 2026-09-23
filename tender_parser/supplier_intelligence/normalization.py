"""Conservative normalization for identity comparisons.

The caller keeps the original values for display and audit.  A normalized name
is only a comparison key, and is not by itself proof that two firms are equal.
"""

from __future__ import annotations

import re
import unicodedata
from email.utils import parseaddr


_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$", re.UNICODE)
_LEGAL_FORM_RE = re.compile(
    r"\b(?:ооо|ао|пао|зао|оао|ип|тд|товарищество\s+с\s+ограниченной\s+ответственностью|"
    r"общество\s+с\s+ограниченной\s+ответственностью)\b",
    re.IGNORECASE,
)
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "mail.ru", "bk.ru", "list.ru", "inbox.ru",
        "internet.ru", "yandex.ru", "ya.ru", "yahoo.com", "outlook.com",
        "hotmail.com", "live.com", "icloud.com", "rambler.ru",
    }
)


def normalize_email(value: str | None) -> str:
    """Return a lowercase mailbox address, or an empty string if malformed."""
    if not value:
        return ""
    value = value.strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    _, address = parseaddr(value)
    address = address.strip().strip("<>;,.").lower()
    return address if _EMAIL_RE.fullmatch(address) else ""


def normalize_phone(value: str | None) -> str:
    """Normalize Russian numbers to +7XXXXXXXXXX and retain valid E.164 others.

    Extensions are deliberately excluded from the identity key; they belong in
    the contact's separate PHONE_EXT field.
    """
    if not value:
        return ""
    main = re.split(r"(?:доб\.?|ext\.?|extension|#)\s*\d+", value, maxsplit=1, flags=re.I)[0]
    digits = re.sub(r"\D", "", main)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        return "+7" + digits
    if main.strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    return ""


def normalize_company(value: str | None) -> str:
    """Produce a comparison key while leaving the official name untouched."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    text = text.replace("&", " и ")
    text = re.sub(r"[«»\"'`„“”]", " ", text)
    text = _LEGAL_FORM_RE.sub(" ", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def is_public_email_domain(email_or_domain: str | None) -> bool:
    """Whether a mailbox domain is unsuitable as a supplier identity key."""
    if not email_or_domain:
        return False
    domain = email_or_domain.rsplit("@", 1)[-1].strip().casefold()
    return domain in PUBLIC_EMAIL_DOMAINS
