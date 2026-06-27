# Env And RTS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe local `.env` support for token-based sources and document the dedicated RTS-Tender integration strategy.

**Architecture:** Add a small dependency-free environment loader, call it from CLI before source construction, and add a `check-env` command. Keep RTS changes documentation-only in this increment so the current parser behavior stays stable while we gather cabinet/API facts.

**Tech Stack:** Python 3, pytest, argparse, local Markdown documentation.

## Global Constraints

- Do not commit real tokens or credentials.
- Do not add external dependencies.
- Existing OS environment variables override `.env`.
- Do not bypass captcha or automate restricted RTS cabinet actions.
- Preserve `python -m tender_parser run`.

---

### Task 1: Env Loader

**Files:**
- Create: `tender_parser/env.py`
- Create: `tests/test_env.py`

**Interfaces:**
- Produces: `load_env_file(path: Path) -> list[str]`
- Produces: `get_env_status(keys: list[str]) -> dict[str, bool]`

- [x] Write tests for comments, quotes, no override, and status.
- [x] Verify tests fail because module is missing.
- [x] Implement minimal parser.
- [x] Verify tests pass.
- [x] Commit as `Add local env loader`.

### Task 2: CLI check-env

**Files:**
- Modify: `tender_parser/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces CLI command: `python -m tender_parser check-env`
- Consumes: `.env` from `--base-dir`

- [x] Write tests for missing and present EAT config.
- [x] Verify tests fail because command is missing.
- [x] Load `.env` before default source construction in `run`.
- [x] Implement `check-env` with masked, boolean-only output.
- [x] Verify CLI tests pass.
- [x] Commit as `Add env configuration check`.

### Task 3: Gitignore, Example, Docs, RTS Foundation

**Files:**
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `docs/rts_tender_foundation_2026-06-28.md`
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`
- Modify: this plan

**Interfaces:**
- Documents local token setup.
- Documents RTS-Tender next implementation path.

- [x] Ignore `.env` and `.env.local`.
- [x] Add `.env.example` with placeholder EAT keys.
- [x] Add RTS foundation document.
- [x] Update README/MEMORY/HANDOFF.
- [ ] Run full tests.
- [ ] Commit as `Document env and RTS foundation`.

### Task 4: Finish

**Files:** no additional source files.

- [ ] Run `pytest -q` in worktree.
- [ ] Merge into `codex/rts-tender-parser`.
- [ ] Run `pytest -q` in main checkout.
- [ ] Run `python -m tender_parser check-env` in main checkout.
- [ ] Push `codex/rts-tender-parser` to `target`.
