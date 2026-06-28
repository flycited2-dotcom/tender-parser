from __future__ import annotations

from dataclasses import replace

from tender_parser.documents import DocumentAnalyzer, DocumentEvidence
from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text


class TenderEnricher:
    def __init__(self, document_analyzer: DocumentAnalyzer) -> None:
        self.document_analyzer = document_analyzer

    def enrich(self, tenders: list[TenderRecord]) -> list[TenderRecord]:
        evidence = self.document_analyzer.analyze()
        if not evidence.summary and not evidence.searchable_text:
            return tenders
        return [self._enrich_one(tender, evidence, len(tenders)) for tender in tenders]

    def _enrich_one(self, tender: TenderRecord, evidence: DocumentEvidence, total_count: int) -> TenderRecord:
        if not _evidence_applies_to_tender(tender, evidence, total_count):
            return tender

        matches = _unique([*tender.document_matches, *evidence.matched_terms, *evidence.regions])
        if not matches:
            return replace(tender, detail_status="documents_checked")

        region = tender.region or (evidence.regions[0] if evidence.regions else None)
        raw_text = " ".join(part for part in [tender.raw_text, *evidence.matched_terms, *evidence.regions] if part)
        return replace(
            tender,
            region=region,
            raw_text=raw_text,
            detail_status="enriched",
            document_matches=matches,
            delivery_region_evidence=evidence.summary,
            source_confidence=max(tender.source_confidence, _confidence(evidence)),
        )


def _evidence_applies_to_tender(tender: TenderRecord, evidence: DocumentEvidence, total_count: int) -> bool:
    if total_count == 1:
        return True
    if tender.source.startswith("import-"):
        return True
    haystack = evidence.searchable_text
    if tender.tender_number and normalize_text(tender.tender_number) in haystack:
        return True
    title = normalize_text(tender.title)
    return bool(title and title in haystack)


def _confidence(evidence: DocumentEvidence) -> float:
    if evidence.matched_terms and evidence.regions:
        return 0.9
    return 0.7


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
