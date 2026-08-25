from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from tender_parser.customers import CUSTOMER_HEADERS


OUTREACH_SCHEMA_VERSION = 1
OUTREACH_HEADERS = [
    "candidate_id",
    "campaign_id",
    "organization_key",
    "organization",
    "organization_type",
    "region",
    "inn",
    "email",
    "phone",
    "contact_person",
    "website",
    "contact_source",
    "source_tender",
    "checked_at",
    "contact_status",
    "contact_basis",
    "consent_status",
    "note",
    "decision",
    "decision_reason",
    "approved_for_send",
    "auto_send_allowed",
]

READY_CONTACT_STATUS = "Готов к обращению"
LOCAL_SUPPRESSION_STATUSES = {
    "не писать": "do_not_contact",
    "отписка": "opted_out",
}
LEGACY_SUPPRESSION_STATUSES = {
    "отправлено": "previously_contacted_horeca",
    "bounced": "bounced_horeca",
    "не писать": "do_not_contact_horeca",
    "отписка": "opted_out_horeca",
}
LEGACY_SUPPRESSION_STAGES = {
    "ответил": "replied_horeca",
    "неактуально": "opted_out_horeca",
    "не писать": "do_not_contact_horeca",
    "отписка": "opted_out_horeca",
}

_REASON_PRIORITY = {
    "opted_out_horeca": 100,
    "do_not_contact_horeca": 95,
    "replied_horeca": 90,
    "bounced_horeca": 80,
    "previously_contacted_horeca": 70,
    "opted_out": 60,
    "do_not_contact": 55,
}
_EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.IGNORECASE)
_SYSTEM_EMAIL_RE = re.compile(
    r"^(?:no-?reply|do-?not-?reply|mailer-daemon|postmaster|root)@|"
    r"@(?:example\.(?:com|net|org)|invalid|localhost)$",
    re.IGNORECASE,
)


@dataclass
class SuppressionIndex:
    """Global cold-outreach suppression shared by HoReCa and tender campaigns."""

    emails: dict[str, str] = field(default_factory=dict)
    organization_keys: dict[str, str] = field(default_factory=dict)

    def add_email(self, email: object, reason: str) -> None:
        normalized = normalize_email(email)
        if not normalized:
            return
        current = self.emails.get(normalized)
        if current is None or _reason_priority(reason) > _reason_priority(current):
            self.emails[normalized] = reason

    def add_organization(self, organization_key: object, reason: str) -> None:
        normalized = str(organization_key or "").strip().casefold()
        if not normalized:
            return
        current = self.organization_keys.get(normalized)
        if current is None or _reason_priority(reason) > _reason_priority(current):
            self.organization_keys[normalized] = reason

    def reason_for(self, email: object, organization_key: object = "") -> str:
        normalized_email = normalize_email(email)
        normalized_organization = str(organization_key or "").strip().casefold()
        reasons = [
            self.emails.get(normalized_email, ""),
            self.organization_keys.get(normalized_organization, ""),
        ]
        return max(reasons, key=_reason_priority, default="")


@dataclass
class OutreachCandidate:
    candidate_id: str
    campaign_id: str
    organization_key: str
    organization: str
    organization_type: str
    region: str
    inn: str
    email: str
    phone: str
    contact_person: str
    website: str
    contact_source: str
    source_tender: str
    checked_at: str
    contact_status: str
    contact_basis: str
    consent_status: str
    note: str
    decision: str
    decision_reason: str
    approved_for_send: bool = False
    auto_send_allowed: bool = False


@dataclass
class OutreachBuildResult:
    candidates: list[OutreachCandidate]

    @property
    def ready_for_campaign_review(self) -> int:
        return sum(item.decision == "ready_for_campaign_review" for item in self.candidates)

    @property
    def needs_contact_review(self) -> int:
        return sum(item.decision == "needs_contact_review" for item in self.candidates)

    @property
    def suppressed(self) -> int:
        return sum(item.decision == "suppressed" for item in self.candidates)

    @property
    def excluded(self) -> int:
        return sum(item.decision == "excluded" for item in self.candidates)

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self.candidates),
            "ready_for_campaign_review": self.ready_for_campaign_review,
            "needs_contact_review": self.needs_contact_review,
            "suppressed": self.suppressed,
            "excluded": self.excluded,
        }


@dataclass(frozen=True)
class OutreachHandoff:
    csv_path: Path
    manifest_path: Path
    run_id: str
    sha256: str
    stats: dict[str, int]


def normalize_email(value: object) -> str:
    match = _EMAIL_RE.search(str(value or "").strip().casefold())
    return match.group(0).rstrip(".") if match else ""


def build_legacy_horeca_suppression(
    rows: Iterable[Sequence[object]],
    headers: Sequence[object],
) -> SuppressionIndex:
    """Convert the old HoReCa mailing ledger into a global no-repeat index.

    Only a real previous touch suppresses a recipient. Rows that were merely
    imported as ``новый`` or ``проверить вручную`` do not claim the address.
    """

    columns = {str(value or "").strip(): index for index, value in enumerate(headers)}
    index = SuppressionIndex()
    for row in rows:
        email = _cell(row, columns.get("Email"))
        status = _normalized_label(_cell(row, columns.get("Статус")))
        stage = _normalized_label(_cell(row, columns.get("Этап")))
        status_reason = LEGACY_SUPPRESSION_STATUSES.get(status, "")
        stage_reason = LEGACY_SUPPRESSION_STAGES.get(stage, "")
        reason = max((status_reason, stage_reason), key=_reason_priority, default="")
        if reason:
            index.add_email(email, reason)
    return index


def build_outreach_candidates(
    customer_rows: Iterable[Sequence[object]],
    *,
    suppression: SuppressionIndex | None = None,
    campaign_id: str = "",
) -> OutreachBuildResult:
    """Build an approval-gated tender outreach queue from customer CRM rows."""

    suppression = suppression or SuppressionIndex()
    candidates = [
        _candidate_from_customer_row(row, suppression=suppression, campaign_id=campaign_id)
        for row in customer_rows
        if any(str(value or "").strip() for value in row)
    ]
    _mark_internal_duplicates(candidates)
    return OutreachBuildResult(candidates)


def write_outreach_handoff(
    result: OutreachBuildResult,
    output_dir: Path,
    *,
    generated_at: datetime,
    run_id: str | None = None,
) -> OutreachHandoff:
    """Write immutable review artifacts; this function can never approve sending."""

    output_dir.mkdir(parents=True, exist_ok=True)
    actual_run_id = run_id or generated_at.strftime("%Y%m%dT%H%M%S")
    safe_run_id = re.sub(r"[^0-9A-Za-z._-]+", "-", actual_run_id).strip("-")
    if not safe_run_id:
        raise ValueError("run_id must contain at least one safe character")

    csv_path = output_dir / f"outreach_candidates_{safe_run_id}.csv"
    csv_text = _candidate_csv(result.candidates)
    _atomic_write_text(csv_path, csv_text, encoding="utf-8-sig")
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    manifest = {
        "schema_version": OUTREACH_SCHEMA_VERSION,
        "run_id": actual_run_id,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "state": "ready_for_review",
        "approved_for_send": False,
        "auto_send_allowed": False,
        "approval_policy": "manual_campaign_approval_required",
        "dedup_policy": "global_horeca_and_tender_history",
        "geography": [
            "Республика Крым",
            "Севастополь",
            "Запорожская область",
            "Херсонская область",
        ],
        "stats": result.stats(),
        "columns": OUTREACH_HEADERS,
        "candidates_csv": csv_path.name,
        "candidates_csv_sha256": digest,
        "idempotency_key": f"tender-outreach-v1:{digest}",
    }
    manifest_path = output_dir / f"outreach_handoff_{safe_run_id}.json"
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _atomic_write_text(
        output_dir / "latest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return OutreachHandoff(csv_path, manifest_path, actual_run_id, digest, result.stats())


def _candidate_from_customer_row(
    row: Sequence[object],
    *,
    suppression: SuppressionIndex,
    campaign_id: str,
) -> OutreachCandidate:
    padded = [*row[: len(CUSTOMER_HEADERS)]]
    padded.extend([""] * (len(CUSTOMER_HEADERS) - len(padded)))
    organization_key = str(padded[0] or "").strip().casefold()
    email = normalize_email(padded[7])
    contact_status = str(padded[14] or "").strip()
    local_reason = LOCAL_SUPPRESSION_STATUSES.get(_normalized_label(contact_status), "")
    global_reason = suppression.reason_for(email, organization_key)

    if not email:
        decision, reason = "excluded", "missing_or_invalid_email"
    elif _SYSTEM_EMAIL_RE.search(email):
        decision, reason = "excluded", "system_or_placeholder_email"
    elif global_reason:
        decision, reason = "suppressed", global_reason
    elif local_reason:
        decision, reason = "suppressed", local_reason
    elif contact_status != READY_CONTACT_STATUS:
        decision, reason = "needs_contact_review", "contact_not_marked_ready"
    else:
        # A public business email or a manually checked contact is not proof of
        # prior consent to receive advertising. Consent/basis is collected in
        # the separate outreach queue and must be evidenced there before a
        # human may promote the row to ready_for_campaign_review.
        decision, reason = "needs_contact_review", "consent_not_confirmed"

    identity = f"tender-outreach-v1|{organization_key}|{email}"
    candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return OutreachCandidate(
        candidate_id=candidate_id,
        campaign_id=campaign_id,
        organization_key=organization_key,
        organization=str(padded[1] or "").strip(),
        organization_type=str(padded[2] or "").strip(),
        region=str(padded[3] or "").strip(),
        inn=str(padded[4] or "").strip(),
        email=email,
        phone=str(padded[8] or "").strip(),
        contact_person=str(padded[9] or "").strip(),
        website=str(padded[10] or "").strip(),
        contact_source=str(padded[11] or "").strip(),
        source_tender=str(padded[12] or "").strip(),
        checked_at=str(padded[13] or "").strip(),
        contact_status=contact_status,
        contact_basis="",
        consent_status="unknown",
        note=str(padded[15] or "").strip(),
        decision=decision,
        decision_reason=reason,
    )


def _mark_internal_duplicates(candidates: list[OutreachCandidate]) -> None:
    claimed_emails: set[str] = set()
    claimed_organizations: set[str] = set()
    order = sorted(
        range(len(candidates)),
        key=lambda index: (_candidate_priority(candidates[index]), candidates[index].candidate_id),
        reverse=True,
    )
    for index in order:
        candidate = candidates[index]
        if not candidate.email or candidate.decision in {"suppressed", "excluded"}:
            continue
        if candidate.email in claimed_emails:
            candidate.decision = "excluded"
            candidate.decision_reason = "duplicate_email_in_current_registry"
            continue
        if candidate.organization_key and candidate.organization_key in claimed_organizations:
            candidate.decision = "excluded"
            candidate.decision_reason = "duplicate_organization_in_current_registry"
            continue
        claimed_emails.add(candidate.email)
        if candidate.organization_key:
            claimed_organizations.add(candidate.organization_key)


def _candidate_priority(candidate: OutreachCandidate) -> tuple[int, int, int, int]:
    decision_rank = {
        "ready_for_campaign_review": 3,
        "needs_contact_review": 2,
        "suppressed": 1,
        "excluded": 0,
    }.get(candidate.decision, 0)
    return (
        decision_rank,
        int(candidate.contact_status == READY_CONTACT_STATUS),
        int(bool(candidate.inn)),
        int(bool(candidate.checked_at)),
    )


def _candidate_csv(candidates: Iterable[OutreachCandidate]) -> str:
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=OUTREACH_HEADERS, delimiter=";")
    writer.writeheader()
    for candidate in candidates:
        writer.writerow(asdict(candidate))
    return buffer.getvalue()


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(content, encoding=encoding, newline="")
    os.replace(tmp_path, path)


def _cell(row: Sequence[object], index: int | None) -> object:
    if index is None or index < 0 or index >= len(row):
        return ""
    return row[index]


def _normalized_label(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _reason_priority(reason: str) -> int:
    return _REASON_PRIORITY.get(reason, 0)
