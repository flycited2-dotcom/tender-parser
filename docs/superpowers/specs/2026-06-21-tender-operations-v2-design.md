# Tender Operations V2

## Goal

Turn the parser into an operational daily collection process: every run must explain source health, remove confident cross-source duplicates, isolate newly discovered actionable tenders, and leave CRM-ready files.

## Scope

- Record the result of every attempted source: status, duration, number of records, and an error or skip reason.
- Prefer official/direct records over aggregator copies when identical tender cards are detected.
- Preserve the current historical SQLite store and additionally identify actionable tenders that appeared for the first time in the database during a run.
- Produce three machine-readable outputs: the full queue (`latest.json`), the new queue (`new_tenders.json`), and a health report (`run_report.json`).
- Add a Windows task-scheduler installer script; it prepares a daily local run but does not create a task until the user launches the installer.

## Non-Goals

- Do not bypass captcha, authentication, or anti-bot controls.
- Do not submit bids.
- Do not add an unreliable new marketplace source in this release. New ETPs remain a separate intake track after a stable public or cabinet channel is confirmed.

## Architecture

`CompositeSource` gains a reporting path that returns collected `TenderRecord` objects plus per-source `SourceHealth` entries. The existing `fetch_keywords` API remains as a compatibility wrapper.

Before filtering and storage, `deduplicate_tenders` merges only high-confidence duplicate cards: normalized title, same price, same deadline date, and compatible target region. The preferred record is selected by source priority, with missing fields filled from the duplicate.

`TenderStorage.upsert_many` returns the records inserted for the first time. The CLI exports the full actionable queue, the new actionable queue, and the health report, then displays concise numbers in the terminal.

## Acceptance Criteria

1. A run creates `latest.json`, `new_tenders.json`, and `run_report.json`.
2. `run_report.json` lists every attempted source with `ok`, `empty`, `skipped`, or `error` status.
3. A duplicate EIS/Rostender card becomes one tender, with the EIS card preferred.
4. First run marks actionable records as new; the same records on the next run are not new.
5. The regular Windows launcher still works. A separate installer script can register a daily scheduled run at a chosen local time.
