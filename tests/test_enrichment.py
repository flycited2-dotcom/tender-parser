from __future__ import annotations

from pathlib import Path

from tender_parser.enrichment import TenderEnricher
from tender_parser.documents import DocumentAnalyzer
from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord


def test_tender_enricher_promotes_document_region_evidence(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    (documents_dir / "specification.txt").write_text(
        "Адрес поставки: г. Севастополь. Требуется поставка многофункциональных устройств.",
        encoding="utf-8",
    )
    tender = TenderRecord(
        title="Поставка оргтехники",
        url="https://example.test/tender/1",
        source="manual",
        price=50_000,
    )

    enriched = TenderEnricher(DocumentAnalyzer(documents_dir)).enrich([tender])[0]

    assert enriched.detail_status == "enriched"
    assert enriched.region == "севастополь"
    assert "многофункциональное устройство" in enriched.document_matches
    assert "севастополь" in enriched.document_matches
    assert "specification.txt" in enriched.delivery_region_evidence
    assert enriched.source_confidence >= 0.9

    evaluated = evaluate_tender(enriched)
    assert evaluated.review_priority == "review"
    assert evaluated.exclude_reason.startswith("требуется проверка")


def test_tender_enricher_marks_documents_checked_when_nothing_matches(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    (documents_dir / "unrelated.txt").write_text("Протокол организационного совещания.", encoding="utf-8")
    tender = TenderRecord(
        title="Неизвестная закупка",
        url="https://example.test/tender/2",
        source="manual",
    )

    enriched = TenderEnricher(DocumentAnalyzer(documents_dir)).enrich([tender])[0]

    assert enriched.detail_status == "documents_checked"
    assert enriched.document_matches == []
    assert enriched.delivery_region_evidence == ""
