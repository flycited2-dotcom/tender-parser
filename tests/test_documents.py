from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile
from pathlib import Path

from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

from tender_parser.documents import DocumentAnalyzer


def test_document_analyzer_extracts_target_terms_region_and_stop_terms(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    (documents_dir / "notice.txt").write_text(
        "Техническое задание: поставка МФУ и принтеров. "
        "Место поставки: Республика Крым, г. Симферополь. "
        "Лекарственные препараты не входят в закупку.",
        encoding="utf-8",
    )

    evidence = DocumentAnalyzer(documents_dir).analyze()

    assert "мфу" in evidence.matched_terms
    assert "принтер" in evidence.matched_terms
    assert "республика крым" in evidence.regions
    assert "симферополь" in evidence.regions
    assert "лекарственные препараты" in evidence.stop_terms
    assert "notice.txt" in evidence.summary


def test_document_analyzer_returns_empty_evidence_for_missing_folder(tmp_path: Path) -> None:
    evidence = DocumentAnalyzer(tmp_path / "documents").analyze()

    assert evidence.matched_terms == []
    assert evidence.regions == []
    assert evidence.stop_terms == []
    assert evidence.summary == ""


def test_document_analyzer_extracts_evidence_from_xlsx(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Предмет", "Место поставки"])
    sheet.append(["Поставка кондиционеров", "Севастополь"])
    workbook.save(documents_dir / "specification.xlsx")

    evidence = DocumentAnalyzer(documents_dir).analyze()

    assert "кондиционер" in evidence.matched_terms
    assert "севастополь" in evidence.regions
    assert "specification.xlsx" in evidence.summary


def test_document_analyzer_extracts_evidence_from_docx(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Поставка сейфов в Херсонская область</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with ZipFile(documents_dir / "terms.docx", "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)

    evidence = DocumentAnalyzer(documents_dir).analyze()

    assert "сейф" in evidence.matched_terms
    assert "херсонская область" in evidence.regions
    assert "terms.docx" in evidence.summary


def test_document_analyzer_extracts_evidence_from_pdf(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(10), FloatObject(20), FloatObject(20)]),
            NameObject("/Contents"): TextStringObject("Поставка МФУ в Симферополь"),
        }
    )
    writer.add_annotation(0, annotation)
    with (documents_dir / "notice.pdf").open("wb") as handle:
        writer.write(handle)

    evidence = DocumentAnalyzer(documents_dir).analyze()

    assert "мфу" in evidence.matched_terms
    assert "симферополь" in evidence.regions
    assert "notice.pdf" in evidence.summary
