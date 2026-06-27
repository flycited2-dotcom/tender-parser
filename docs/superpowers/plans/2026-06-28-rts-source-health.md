# RTS Source Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make RTS-Tender a first-class parsed source with endpoint-level diagnostics instead of a mostly hidden fallback.

**Architecture:** Add `RtsPublicSource.fetch_with_report()` that returns `SourceFetchResult` with one `SourceHealth` row per RTS endpoint. Teach `CompositeSource` to consume `fetch_with_report()` from non-composite sources, then move `RtsPublicSource` into the default live source layer so it runs in normal collection.

**Tech Stack:** Python 3, `requests`, `pytest`, existing `SourceFetchResult` / `SourceHealth` report model.

## Global Constraints

- Do not bypass captcha or automate restricted RTS cabinet actions.
- Do not add external dependencies.
- Keep `RtsPublicSource.fetch_keywords()` compatible for existing callers.
- A failed RTS endpoint must not fail the whole parser when other sources return tenders.
- Use focused TDD: write failing tests first, then minimal implementation.

---

### Task 1: RTS Endpoint Health

**Files:**
- Modify: `tender_parser/sources/rts.py`
- Modify: `tender_parser/run_report.py`
- Modify: `tests/test_rts_source.py`

**Interfaces:**
- Produces: `RtsPublicSource.fetch_with_report(keywords: Iterable[str]) -> SourceFetchResult`
- Preserves: `RtsPublicSource.fetch_keywords(keywords: Iterable[str]) -> list[TenderRecord]`
- Produces statuses: `ok`, `empty`, `blocked`, `timeout`, `ssl_error`, `error`

- [x] Add a failing test where one RTS endpoint is blocked and another returns tenders.
- [x] Verify the test fails because `RtsPublicSource` has no `fetch_with_report`.
- [x] Implement endpoint-level reporting and exception classification.
- [x] Make `fetch_keywords()` call `fetch_with_report()` and preserve old all-failed behavior.
- [x] Verify `tests/test_rts_source.py` passes.
- [x] Commit as `Add RTS endpoint health report`.

### Task 2: Composite Report-Aware Sources

**Files:**
- Modify: `tender_parser/sources/composite.py`
- Modify: `tests/test_composite_source.py`

**Interfaces:**
- Consumes: any source with `fetch_with_report(keywords) -> SourceFetchResult`
- Produces: composite health rows from that source without replacing them with class-level health.

- [x] Add a failing test proving `CompositeSource` uses a normal source's `fetch_with_report`.
- [x] Verify the test fails because only nested `CompositeSource` gets special handling.
- [x] Implement a small helper that detects and calls report-aware sources.
- [x] Verify `tests/test_composite_source.py` passes.
- [x] Commit as `Use source-level health reports in composite`.

### Task 3: Run RTS In The Main Source Layer

**Files:**
- Modify: `tender_parser/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces default source order: `EtpGpbRssSource`, `TenderProSource`, `Torgi82Source`, `B2BCenterSource`, `EatIntegrationSource`, `EisZakupkiSource`, `RostenderSource`, `RtsPublicSource`

- [x] Update the default source test to expect `RtsPublicSource` in the main layer.
- [x] Verify the test fails because RTS is still fallback-only.
- [x] Move RTS into the main live layer and remove the extra fallback wrapper.
- [x] Verify CLI tests pass.
- [x] Commit as `Run RTS in main tender source layer`.

### Task 4: Docs And Finish

**Files:**
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`
- Modify: this plan

- [x] Document that RTS now runs in the main source layer with endpoint health.
- [ ] Run `pytest -q` in the worktree.
- [ ] Merge into `codex/rts-tender-parser`.
- [ ] Run `pytest -q` in the main checkout.
- [ ] Push `codex/rts-tender-parser` to `target`.
