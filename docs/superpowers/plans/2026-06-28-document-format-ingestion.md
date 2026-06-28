# Document Format Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `DocumentAnalyzer` read PDF, DOCX, and XLSX files from `documents/`.

**Architecture:** Extend the existing document reader dispatch in `tender_parser/documents.py`. Keep extraction output unchanged so CLI, enrichment, exports, and storage continue using the same evidence flow.

**Tech Stack:** Python 3, stdlib `zipfile`/`xml.etree.ElementTree`, existing `openpyxl`, new pinned dependency `pypdf==6.14.2`, pytest.

## Global Constraints

- Do not add OCR or image-only PDF support in this increment.
- Do not automate closed cabinets or captcha-protected downloads.
- Keep `documents/` ignored by Git.
- Use TDD before changing production code.

---

### Task 1: XLSX And DOCX Document Readers

**Files:**
- Modify: `tests/test_documents.py`
- Modify: `tender_parser/documents.py`

**Interfaces:**
- Consumes: `DocumentAnalyzer(documents_dir: Path).analyze() -> DocumentEvidence`
- Produces: support for `.xlsx` and `.docx` suffixes in `SUPPORTED_SUFFIXES`

- [x] Add failing XLSX document test.
- [x] Add failing DOCX document test.
- [x] Implement `_read_xlsx_text(path: Path) -> str`.
- [x] Implement `_read_docx_text(path: Path) -> str`.
- [x] Run `pytest tests/test_documents.py -q`.
- [x] Commit `Add office document readers`.

### Task 2: PDF Document Reader

**Files:**
- Modify: `requirements.txt`
- Modify: `tests/test_documents.py`
- Modify: `tender_parser/documents.py`

**Interfaces:**
- Produces: support for `.pdf` suffix in `SUPPORTED_SUFFIXES`
- Uses: `pypdf.PdfReader`

- [x] Add `pypdf==6.14.2` to `requirements.txt`.
- [x] Install requirements in the local venv.
- [x] Add failing PDF document test using `pypdf.PdfWriter` with an embedded text annotation.
- [x] Implement `_read_pdf_text(path: Path) -> str`.
- [x] Run `pytest tests/test_documents.py -q`.
- [x] Commit `Add PDF document reader`.

### Task 3: Docs And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`
- Modify: this plan

- [x] Document PDF/DOCX/XLSX support and image-only PDF limitation.
- [x] Run `pytest -q` in worktree.
- [ ] Merge into `codex/rts-tender-parser`.
- [ ] Run `pytest -q` in main checkout.
- [ ] Push `codex/rts-tender-parser` to `target`.
