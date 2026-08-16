from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from pypdf import PdfWriter

from tender_parser.case_preflight import TextSegment, _extract_word_table_items, analyze_case_documents, export_preflight
from tender_parser.cli import run
from tender_parser.tender_case import initialize_case


def _create_complete_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "cases" / "preflight-1"
    initialize_case(case_dir, case_id="preflight-1", title="Поставка МФУ")
    documents = case_dir / "documents"
    (documents / "notice.txt").write_text(
        "Извещение об осуществлении закупки. НМЦК: 150 000,00 руб. "
        "Оплата в течение 7 рабочих дней после приемки.",
        encoding="utf-8",
    )
    (documents / "contract.txt").write_text(
        "Проект контракта. Срок поставки товара в течение 10 календарных дней. "
        "Гарантийный срок составляет 12 месяцев.",
        encoding="utf-8",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Техническое задание"
    sheet.append(["№", "Наименование товара", "Количество", "Единица измерения", "Технические характеристики"])
    sheet.append([1, "МФУ лазерное", 2, "шт.", "A4; скорость не менее 30 стр/мин"])
    workbook.save(documents / "specification.xlsx")
    return case_dir


def test_preflight_extracts_documents_metadata_items_and_evidence(tmp_path: Path) -> None:
    case_dir = _create_complete_case(tmp_path)

    result = analyze_case_documents(case_dir)

    assert result.ready_for_product_search is True
    assert set(result.document_types_found) >= {"notice", "contract_draft", "technical_specification"}
    assert result.metadata_candidates["nmck"][0]["value"] == "150 000,00"
    assert result.metadata_candidates["payment_days"][0]["value"] == "7"
    assert result.metadata_candidates["delivery_days"][0]["value"] == "10"
    assert len(result.item_candidates) == 1
    item = result.item_candidates[0]
    assert item.name == "МФУ лазерное"
    assert item.quantity == "2"
    assert item.source == "specification.xlsx"
    assert "строка 2" in item.locator
    assert any(finding.code == "guarantee" for finding in result.findings)


def test_preflight_exports_drafts_without_overwriting_owner_items(tmp_path: Path) -> None:
    case_dir = _create_complete_case(tmp_path)
    original_items = (case_dir / "items.csv").read_bytes()

    outputs = export_preflight(analyze_case_documents(case_dir), case_dir / "output")

    assert (case_dir / "items.csv").read_bytes() == original_items
    assert set(outputs) == {"json", "markdown", "items", "questions"}
    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["case_id"] == "preflight-1"
    assert "owner_status" in outputs["items"].read_text(encoding="utf-8-sig")
    assert "Автоматический черновик" in outputs["markdown"].read_text(encoding="utf-8")


def test_preflight_blocks_unsearchable_pdf_and_missing_core_documents(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "scan-only"
    initialize_case(case_dir, case_id="scan-only", title="Скан")
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with (case_dir / "documents" / "scan.pdf").open("wb") as handle:
        writer.write(handle)

    result = analyze_case_documents(case_dir)

    codes = {finding.code for finding in result.findings}
    assert result.ready_for_product_search is False
    assert "ocr_required" in codes
    assert "missing_technical_specification" in codes
    assert "missing_contract_draft" in codes
    assert "missing_notice" in codes


def test_case_preflight_cli_writes_outputs(tmp_path: Path) -> None:
    _create_complete_case(tmp_path)

    assert run(["case-preflight", "--base-dir", str(tmp_path), "--case-id", "preflight-1"]) == 0
    output = tmp_path / "cases" / "preflight-1" / "output"
    assert (output / "preflight.json").exists()
    assert (output / "preflight.md").exists()
    assert (output / "items_draft.csv").exists()
    assert (output / "customer_questions.txt").exists()


def test_xlsx_preamble_is_not_mistaken_for_item_header(tmp_path: Path) -> None:
    case_dir = _create_complete_case(tmp_path)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Расчет цены"])
    sheet.append(
        [
            "Метод определения цены: количество закупаемого товара, номер источника, "
            "наименование позиции и объем закупки используются в расчете."
        ]
    )
    sheet.append(["№ п/п", "Наименование товара, ОКПД2/КТРУ", "ед. изм.", "Кол-во"])
    sheet.append([1, "Блок фотобарабана 26.20.40.120", "шт", 15])
    workbook.save(case_dir / "documents" / "nmck.xlsx")

    result = analyze_case_documents(case_dir)

    nmck_items = [item for item in result.item_candidates if item.source == "nmck.xlsx"]
    assert len(nmck_items) == 1
    assert nmck_items[0].line_id == "1"
    assert nmck_items[0].name == "Блок фотобарабана 26.20.40.120"
    assert nmck_items[0].quantity == "15"


def test_preflight_reads_supported_files_inside_zip_archive(tmp_path: Path) -> None:
    case_dir = _create_complete_case(tmp_path)
    with ZipFile(case_dir / "documents" / "extra.zip", "w", ZIP_DEFLATED) as archive:
        archive.writestr("Описание объекта закупки.txt", "Описание объекта закупки. Технические характеристики товара.")

    result = analyze_case_documents(case_dir)

    archived = [document for document in result.documents if document.path == "extra.zip"]
    extracted = [document for document in result.documents if document.path.startswith("extra.zip::")]
    assert archived[0].document_type == "archive"
    assert extracted[0].searchable is True
    assert extracted[0].document_type == "technical_specification"


def test_word_table_rows_are_assembled_into_items_with_specs() -> None:
    segments = [
        TextSegment(
            "ТЗ.doc",
            "таблица 1, строка 1",
            "№ п/п | Наименование объекта закупки | Описание | Ед. измерения | Кол-во",
        ),
        TextSegment(
            "ТЗ.doc",
            "таблица 1, строка 4",
            "1. | Блок фотобарабана/26.20.40.120 | 1 | Совместимость | Brother HL-L5210DW | неизменяемое | Штука | 15",
        ),
        TextSegment(
            "ТЗ.doc",
            "таблица 1, строка 5",
            " |  | 2 | Ресурс работы | ≥ 57 000 листов | конкретное значение |  | ",
        ),
    ]

    items = _extract_word_table_items(segments, "ТЗ.doc")

    assert len(items) == 1
    assert items[0].name == "Блок фотобарабана 26.20.40.120"
    assert items[0].quantity == "15"
    assert "Brother HL-L5210DW" in items[0].required_specs
    assert "57 000" in items[0].required_specs
