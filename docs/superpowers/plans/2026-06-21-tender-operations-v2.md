# Tender Operations V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make daily tender collection transparent, deduplicated, and ready for CRM ingestion.

**Architecture:** Add a reporting result alongside `CompositeSource.fetch_keywords`, normalize high-confidence cross-source duplicates before filtering, return first-seen records from SQLite, and export full/new/health JSON files. A PowerShell installer creates an optional Windows daily task.

**Tech Stack:** Python 3.14, `requests`, BeautifulSoup, SQLite, pytest, PowerShell Task Scheduler.

## Global Constraints

- Keep `TenderSource.fetch_keywords` backward compatible.
- Do not bypass captcha or require credentials that the user has not supplied.
- Use TDD: a failing focused test precedes each production change.
- Keep `data/`, `exports/`, and `logs/` local and untracked.

---

### Task 1: Source Health Report

**Files:**
- Create: `tender_parser/run_report.py`
- Modify: `tender_parser/sources/composite.py`
- Test: `tests/test_composite_source.py`

**Interfaces:**
- Produces `SourceHealth(source, status, found, elapsed_seconds, detail)`.
- Produces `SourceFetchResult(tenders, health)`.
- Adds `CompositeSource.fetch_with_report(keywords) -> SourceFetchResult`.

- [ ] Write a failing test where one source raises `SourceFetchError` and the next returns one record; assert one `error` and one `ok` health entry.
- [ ] Run `python -m pytest tests/test_composite_source.py -q`; expect failure because `fetch_with_report` does not exist.
- [ ] Implement `SourceHealth`, `SourceFetchResult`, and recursive composite reporting without changing `fetch_keywords` callers.
- [ ] Run the focused test; expect pass.

### Task 2: Cross-Source Dedupe

**Files:**
- Create: `tender_parser/dedup.py`
- Modify: `tender_parser/cli.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Produces `deduplicate_tenders(tenders) -> DeduplicationResult`.
- Merges cards only when normalized title, rounded price, deadline date, and target region are equal.
- Prefers `eis-zakupki` over `rostender` and fills missing preferred fields from the alternate card.

- [ ] Write a failing test for matching EIS/Rostender cards and assert one EIS record remains.
- [ ] Run `python -m pytest tests/test_dedup.py -q`; expect import failure.
- [ ] Implement the smallest deterministic merge and use it in `cli.run` before `evaluate_tender`.
- [ ] Run the focused test; expect pass.

### Task 3: New-Tender Queue and JSON Run Report

**Files:**
- Modify: `tender_parser/storage.py`
- Modify: `tender_parser/exporters/json_exporter.py`
- Modify: `tender_parser/cli.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_exporters.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Changes `TenderStorage.upsert_many(tenders)` to return first-seen `TenderRecord` objects.
- Adds `export_run_report(report, output_path)`.
- Creates `exports/new_tenders.json` and `exports/run_report.json` on each successful run.

- [ ] Write a failing storage test that upserts a record twice and asserts it is returned only by the first call.
- [ ] Run `python -m pytest tests/test_storage.py -q`; expect assertion failure.
- [ ] Implement first-seen detection before the SQLite upsert.
- [ ] Write failing exporter/CLI tests for `new_tenders.json` and `run_report.json`.
- [ ] Implement JSON export and CLI calls, then print raw, unique, new, and source-health summaries.
- [ ] Run focused storage/exporter/CLI tests; expect pass.

### Task 4: Daily Windows Run

**Files:**
- Create: `Запустить_парсер_тихо.bat`
- Create: `Настроить_ежедневный_запуск.ps1`
- Modify: `README.md`
- Test: manual PowerShell syntax check

**Interfaces:**
- `Настроить_ежедневный_запуск.ps1 -Time "08:00"` registers `Tender Parser Daily`.
- The quiet launcher writes stdout/stderr to `logs/daily.log` and does not pause or open Explorer.

- [ ] Add scripts with project-relative paths and no hard-coded user directory.
- [ ] Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\Настроить_ежедневный_запуск.ps1 -WhatIf`; expect a schedule preview and no task creation.
- [ ] Document manual launch, scheduled install, and CRM file meanings.

### Task 5: Verify and Publish

**Files:**
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`

- [ ] Run `python -m pytest -q`; expect all tests passing.
- [ ] Run `python -m tender_parser run`; verify three JSON outputs and source health report.
- [ ] Update handoff with current source counts and health behavior.
- [ ] Commit scoped changes and push `codex/rts-tender-parser` to `target`.
