# Deep Enrichment Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe deep-enrichment foundation: cabinet/export imports, text document evidence, richer tender fields, and CRM-ready exports.

**Architecture:** Extend `TenderRecord` with enrichment fields, persist/export them, then add two independent inputs: `ImportFolderSource` for user-provided cabinet exports and `DocumentAnalyzer`/`TenderEnricher` for text evidence. Wire the enricher into CLI before filtering so imports and evidence flow through existing dedup, filters, storage, Excel, and JSON.

**Tech Stack:** Python 3, stdlib `csv`/`xml.etree.ElementTree`/`json`, existing `openpyxl`, pytest, existing source/report/export models.

## Global Constraints

- Do not commit real tokens, cookies, cabinet exports, or downloaded documents.
- Do not bypass captcha or automate restricted cabinet actions.
- Do not add external dependencies in this increment.
- If `imports/` or `documents/` are empty, `python -m tender_parser run` must behave like the current parser.
- Use TDD for each behavior change.

---

### Task 1: Enrichment Fields

**Files:**
- Modify: `tender_parser/models.py`
- Modify: `tender_parser/storage.py`
- Modify: `tender_parser/exporters/json_exporter.py`
- Modify: `tender_parser/exporters/excel.py`
- Modify/Create tests in `tests/`

**Interfaces:**
- Produces `TenderRecord.detail_status: str`
- Produces `TenderRecord.document_matches: list[str]`
- Produces `TenderRecord.delivery_region_evidence: str`
- Produces `TenderRecord.source_confidence: float`

- [ ] Add failing tests for JSON export fields.
- [ ] Add failing tests for SQLite round-trip and legacy migration.
- [ ] Add failing tests for Excel headers.
- [ ] Implement model fields, storage columns, JSON fields, Excel headers.
- [ ] Run targeted tests and commit `Add enrichment fields to tender records`.

### Task 2: Import Folder Source

**Files:**
- Create: `tender_parser/sources/imports.py`
- Modify: `.gitignore`
- Create: `tests/test_import_source.py`

**Interfaces:**
- Produces `ImportFolderSource(imports_dir: Path).fetch_with_report(keywords: list[str]) -> SourceFetchResult`
- Supports `.csv`, `.xlsx`, `.xml`

- [ ] Add failing CSV import test.
- [ ] Add failing XLSX import test.
- [ ] Add failing XML import test.
- [ ] Implement parsers with header aliases and safe partial records.
- [ ] Run targeted tests and commit `Add import folder source`.

### Task 3: Document Evidence And Enricher

**Files:**
- Create: `tender_parser/documents.py`
- Create: `tender_parser/enrichment.py`
- Create: `tests/test_documents.py`
- Create: `tests/test_enrichment.py`

**Interfaces:**
- Produces `DocumentAnalyzer(documents_dir: Path).analyze() -> DocumentEvidence`
- Produces `TenderEnricher(document_analyzer: DocumentAnalyzer).enrich(tenders: list[TenderRecord]) -> list[TenderRecord]`

- [ ] Add failing document evidence test.
- [ ] Add failing tender enrichment test for region evidence and matches.
- [ ] Implement text document reading and evidence extraction.
- [ ] Implement `TenderEnricher`.
- [ ] Run targeted tests and commit `Add document evidence enrichment`.

### Task 4: CLI Integration

**Files:**
- Modify: `tender_parser/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- CLI reads `imports/` and `documents/` under `--base-dir`.
- Source health includes `ImportFolderSource`.

- [ ] Add failing CLI test proving an imported CSV row reaches `latest.json`.
- [ ] Wire `ImportFolderSource` into `run`.
- [ ] Wire `DocumentAnalyzer` and `TenderEnricher` before `evaluate_tender`.
- [ ] Run CLI tests and commit `Wire deep enrichment into CLI`.

### Task 5: Docs And Finish

**Files:**
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`
- Modify: this plan

- [ ] Document `imports/`, `documents/`, enrichment fields, and current binary document limitation.
- [ ] Run `pytest -q` in worktree.
- [ ] Merge into `codex/rts-tender-parser`.
- [ ] Run `pytest -q` in main checkout.
- [ ] Push `codex/rts-tender-parser` to `target`.
