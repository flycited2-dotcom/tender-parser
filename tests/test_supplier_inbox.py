from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from tender_parser.supplier_inbox import SupplierInbox


def _setup(tmp_path: Path) -> tuple[SupplierInbox, bytes]:
    catalog = tmp_path / "supplier_catalog"
    catalog.mkdir()
    (catalog / "suppliers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suppliers": [
                    {
                        "id": "promet",
                        "name": "ПРОМЕТ",
                        "enabled": True,
                        "email_senders": ["prices@promet.test"],
                        "file_globs": ["private/promet/inbox/*.xlsx"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Артикул", "Наименование", "Дилерская цена"])
    sheet.append(["A-1", "Шкаф архивный", 1234])
    buffer = BytesIO()
    workbook.save(buffer)
    return SupplierInbox(catalog), buffer.getvalue()


def test_trusted_email_is_saved_indexed_and_deduplicated(tmp_path: Path) -> None:
    inbox, payload = _setup(tmp_path)

    first = inbox.accept_bytes(
        payload,
        filename="Прайс.xlsx",
        channel="gmail",
        sender="Promet <prices@promet.test>",
        message_id="m1",
    )
    second = inbox.accept_bytes(
        payload,
        filename="Прайс копия.xlsx",
        channel="gmail",
        sender="prices@promet.test",
        message_id="m2",
    )

    assert first.status == "accepted"
    assert first.supplier_id == "promet"
    assert first.indexed_products == 1
    assert second.status == "duplicate"
    assert len(list((inbox.private_dir / "promet" / "inbox").glob("*.xlsx"))) == 1


def test_unknown_sender_is_quarantined_and_not_indexed(tmp_path: Path) -> None:
    inbox, payload = _setup(tmp_path)

    result = inbox.accept_bytes(
        payload,
        filename="unexpected.xlsx",
        channel="gmail",
        sender="unknown@example.test",
    )

    assert result.status == "quarantined"
    assert "quarantine/inbox" in result.path
    assert result.indexed_products == 0

    accepted_later = inbox.accept_bytes(
        payload,
        filename="unexpected.xlsx",
        channel="manual",
        supplier_id="promet",
    )
    assert accepted_later.status == "accepted"
    assert accepted_later.indexed_products == 1


def test_unsupported_attachment_is_rejected(tmp_path: Path) -> None:
    inbox, _ = _setup(tmp_path)
    result = inbox.accept_bytes(
        b"MZ",
        filename="price.exe",
        channel="telegram",
        supplier_id="promet",
    )
    assert result.status == "rejected"
