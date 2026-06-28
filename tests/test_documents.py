from __future__ import annotations

from pathlib import Path

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
