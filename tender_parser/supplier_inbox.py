from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from tender_parser.suppliers import SupplierCatalog, SupplierDefinition


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ALLOWED_SUFFIXES = {
    ".xlsx",
    ".xlsm",
    ".xls",
    ".csv",
    ".pdf",
    ".docx",
    ".doc",
    ".zip",
    ".rar",
}
INDEXABLE_SUFFIXES = {".xlsx", ".xlsm"}


@dataclass(frozen=True)
class SupplierIntakeResult:
    status: str
    supplier_id: str = ""
    path: str = ""
    sha256: str = ""
    indexed_products: int = 0
    detail: str = ""


class SupplierInbox:
    """Accept supplier attachments without exposing them to the public repository."""

    def __init__(self, catalog_dir: Path) -> None:
        self.catalog_dir = catalog_dir.resolve()
        self.private_dir = self.catalog_dir / "private"
        self.ledger_path = self.private_dir / "intake_log.jsonl"
        self.catalog = SupplierCatalog(self.catalog_dir)

    def accept_bytes(
        self,
        payload: bytes,
        *,
        filename: str,
        channel: str,
        sender: str = "",
        supplier_id: str = "",
        message_id: str = "",
        received_at: datetime | None = None,
        auto_register: bool = False,
    ) -> SupplierIntakeResult:
        if not payload:
            return SupplierIntakeResult(status="rejected", detail="пустой файл")
        if len(payload) > MAX_ATTACHMENT_BYTES:
            return SupplierIntakeResult(status="rejected", detail="файл больше 25 МБ")
        safe_name = _safe_filename(filename)
        suffix = Path(safe_name).suffix.casefold()
        if suffix not in ALLOWED_SUFFIXES:
            return SupplierIntakeResult(
                status="rejected", detail=f"формат {suffix or 'без расширения'} не разрешён"
            )

        definitions = self._definitions()
        resolved_supplier = supplier_id.strip().casefold()
        if not resolved_supplier:
            resolved_supplier = _supplier_for_sender(sender, definitions)
        trusted = definitions.get(resolved_supplier)
        if trusted is None and auto_register:
            resolved_supplier = self._register_supplier(sender, resolved_supplier)
            definitions = self._definitions()
            trusted = definitions.get(resolved_supplier)
        if trusted is None or not trusted.enabled:
            return self._store(
                payload,
                safe_name=safe_name,
                supplier_id="quarantine",
                status="quarantined",
                channel=channel,
                sender=sender,
                message_id=message_id,
                received_at=received_at,
                detail="поставщик не определён; файл сохранён для ручной проверки",
            )

        result = self._store(
            payload,
            safe_name=safe_name,
            supplier_id=trusted.supplier_id,
            status="accepted" if suffix in INDEXABLE_SUFFIXES else "stored",
            channel=channel,
            sender=sender,
            message_id=message_id,
            received_at=received_at,
            detail=(
                "принят и добавлен в индекс"
                if suffix in INDEXABLE_SUFFIXES
                else "сохранён; для этого формата нужен отдельный адаптер"
            ),
        )
        if result.status == "duplicate" or suffix not in INDEXABLE_SUFFIXES:
            return result
        index_status = self.catalog.refresh(force=True)
        return SupplierIntakeResult(
            **{
                **asdict(result),
                "indexed_products": index_status.product_count,
                "detail": (
                    result.detail
                    if index_status.status not in {"error", "partial"}
                    else f"{result.detail}; индекс: {index_status.status} {index_status.detail}".strip()
                ),
            }
        )

    def accept_file(self, path: Path, **metadata: object) -> SupplierIntakeResult:
        resolved = path.resolve()
        try:
            payload = resolved.read_bytes()
        except OSError as exc:
            return SupplierIntakeResult(
                status="rejected", detail=f"не удалось прочитать файл: {exc.__class__.__name__}"
            )
        return self.accept_bytes(payload, filename=resolved.name, **metadata)  # type: ignore[arg-type]

    def _definitions(self) -> dict[str, SupplierDefinition]:
        definitions = self.catalog._load_definitions()
        return {item.supplier_id: item for item in definitions}

    def _register_supplier(self, sender: str, supplier_hint: str) -> str:
        address = _email_address(sender)
        candidate = supplier_hint.strip().casefold()
        if not candidate and address:
            domain = address.rsplit("@", 1)[-1].split(".", 1)[0]
            candidate = domain
        candidate = re.sub(r"[^a-z0-9_-]+", "-", candidate).strip("-")
        if not candidate:
            candidate = "supplier-" + hashlib.sha256(sender.encode("utf-8")).hexdigest()[:8]
        name = candidate.replace("-", " ").replace("_", " ").upper()
        manifest_path = self.private_dir / "suppliers_auto.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {"schema_version": 1, "suppliers": []}
        suppliers = list(payload.get("suppliers", []))
        existing = next((item for item in suppliers if item.get("id") == candidate), None)
        if existing is None:
            suppliers.append(
                {
                    "id": candidate,
                    "name": name,
                    "email_senders": [address] if address else [],
                    "enabled": True,
                    "priority": 100,
                    "file_globs": [
                        f"private/{candidate}/inbox/*.xlsx",
                        f"private/{candidate}/inbox/*.xlsm",
                    ],
                    "tender_categories": [],
                }
            )
        elif address and address not in existing.get("email_senders", []):
            existing.setdefault("email_senders", []).append(address)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": 1, "suppliers": suppliers},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(manifest_path)
        return candidate

    def _store(
        self,
        payload: bytes,
        *,
        safe_name: str,
        supplier_id: str,
        status: str,
        channel: str,
        sender: str,
        message_id: str,
        received_at: datetime | None,
        detail: str,
    ) -> SupplierIntakeResult:
        digest = hashlib.sha256(payload).hexdigest()
        duplicate = self._duplicate_path(digest, supplier_id)
        if duplicate:
            return SupplierIntakeResult(
                status="duplicate",
                supplier_id=supplier_id,
                path=duplicate,
                sha256=digest,
                detail="такой файл уже сохранён",
            )
        timestamp = (received_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
        inbox = self.private_dir / supplier_id / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / f"{timestamp}_{digest[:10]}_{safe_name}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
        relative = target.relative_to(self.catalog_dir).as_posix()
        event = {
            "received_at": (received_at or datetime.now()).isoformat(timespec="seconds"),
            "channel": channel,
            "sender": sender,
            "message_id": message_id,
            "supplier_id": supplier_id,
            "filename": safe_name,
            "path": relative,
            "size_bytes": len(payload),
            "sha256": digest,
            "status": status,
        }
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return SupplierIntakeResult(
            status=status,
            supplier_id=supplier_id,
            path=relative,
            sha256=digest,
            detail=detail,
        )

    def _duplicate_path(self, digest: str, supplier_id: str) -> str:
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                event.get("sha256") == digest
                and event.get("supplier_id") == supplier_id
            ):
                return str(event.get("path", ""))
        return ""


def _supplier_for_sender(
    sender: str, definitions: dict[str, SupplierDefinition]
) -> str:
    address = _email_address(sender)
    for item in definitions.values():
        if address and address in {value.casefold() for value in item.email_senders}:
            return item.supplier_id
    return ""


def _email_address(sender: str) -> str:
    address = sender.strip().casefold()
    match = re.search(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+", address)
    return match.group(0) if match else ""


def _safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name.strip()
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:180] or "price-list.bin")
