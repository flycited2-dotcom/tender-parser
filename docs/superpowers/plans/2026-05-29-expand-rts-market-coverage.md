# Expand RTS Market Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase public parsing coverage by querying multiple RTS-market endpoints and region-first search terms without mixing in cabinet authorization.

**Architecture:** Keep `RtsPublicSource` as the public RTS-market source, but make it endpoint-driven. Each endpoint has a base URL, source label, and optional region hint; parsing remains shared because the market table markup is expected to be compatible. CLI continues to consume one source interface and preserves reports if every public source is blocked or unavailable.

**Tech Stack:** Python 3, `requests`, `BeautifulSoup`, `pytest`, SQLite, openpyxl.

---

### Task 1: Endpoint Model and URL Builder

**Files:**
- Modify: `tender_parser/sources/rts.py`
- Modify: `tender_parser/config.py`
- Test: `tests/test_rts_source.py`

- [ ] **Step 1: Write failing tests**

Add tests that expect custom endpoint URLs and region hints:

```python
def test_build_search_url_accepts_custom_base_url() -> None:
    url = build_search_url(
        "МФУ",
        page_index=1,
        base_url="https://zakupki-simferopol.rts-tender.ru/market/",
    )

    assert url.startswith("https://zakupki-simferopol.rts-tender.ru/market/?")
    assert "f_keyword=" in url
    assert "from=20" in url


def test_parse_market_page_applies_region_hint() -> None:
    html = Path("tests/fixtures/rts_market_sample.html").read_text(encoding="utf-8")

    tenders = parse_market_page(
        html,
        source_url="https://zakupki-simferopol.rts-tender.ru/market/",
        source_name="rts-zakupki-simferopol",
        region_hint="Симферополь",
    )

    assert tenders[0].source == "rts-zakupki-simferopol"
    assert tenders[0].region == "Симферополь"
```

- [ ] **Step 2: Run red test**

Run:

```powershell
python -m pytest tests/test_rts_source.py -v
```

Expected: fail because `build_search_url` and `parse_market_page` do not yet accept those arguments.

- [ ] **Step 3: Implement minimal endpoint support**

Add an endpoint dataclass and optional arguments to `build_search_url` / `parse_market_page`. Preserve existing defaults for old tests.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_rts_source.py -v
```

Expected: pass.

### Task 2: Multi-Endpoint Fetching

**Files:**
- Modify: `tender_parser/sources/rts.py`
- Test: `tests/test_rts_source.py`

- [ ] **Step 1: Write failing tests**

Add fake sessions showing that `fetch_keywords` queries more than one endpoint and deduplicates repeated tender numbers:

```python
def test_fetch_keywords_queries_all_configured_endpoints() -> None:
    session = FakeSession(html_by_host={
        "one.rts-tender.ru": SAMPLE_HTML,
        "two.rts-tender.ru": SAMPLE_HTML_WITH_OTHER_NUMBER,
    })
    source = RtsPublicSource(session=session, endpoints=[
        RtsMarketEndpoint("https://one.rts-tender.ru/market/", "rts-one", None),
        RtsMarketEndpoint("https://two.rts-tender.ru/market/", "rts-two", "Крым"),
    ])

    tenders = source.fetch_keywords(["МФУ"])

    assert len(tenders) == 2
    assert {t.source for t in tenders} == {"rts-one", "rts-two"}
```

- [ ] **Step 2: Run red test**

Run:

```powershell
python -m pytest tests/test_rts_source.py -v
```

Expected: fail because `RtsPublicSource` has no endpoint list.

- [ ] **Step 3: Implement**

Loop over endpoints first, then keywords, using endpoint base URL/source/region hint. Keep per-run dedupe by tender number when available, otherwise by clean URL.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_rts_source.py -v
```

Expected: pass.

### Task 3: Expanded Search Terms and Failure Handling

**Files:**
- Modify: `tender_parser/config.py`
- Modify: `tender_parser/cli.py`
- Modify: `tender_parser/sources/rts.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_rts_source.py`

- [ ] **Step 1: Write failing tests**

Test that `_all_keywords()` includes broad aliases and region terms, and that CLI keeps old exports if all public endpoints are blocked/unavailable.

- [ ] **Step 2: Run red tests**

Run:

```powershell
python -m pytest tests/test_cli.py tests/test_rts_source.py -v
```

Expected: fail because new aliases and all-source failure behavior are not implemented.

- [ ] **Step 3: Implement**

Add `BROAD_SEARCH_TERMS`, `REGION_SEARCH_TERMS`, and `RTS_MARKET_ENDPOINTS` to config. Update `_all_keywords()` to include category terms, broad aliases, and region terms. Convert all-source failure into a single fetch error that CLI handles without overwriting reports.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest -v
python -m tender_parser run
```

Expected: tests pass. Live run either produces a larger report or exits with code `2` and preserves previous reports if RTS blocks the session.

### Task 4: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`

- [ ] **Step 1: Update docs**

Describe public multi-endpoint mode, review sheet, captcha preservation, and the next separate cabinet-auth step.

- [ ] **Step 2: Verify docs are consistent**

Run:

```powershell
python -m pytest -v
git diff --stat
```

Expected: tests pass and diff contains only planned files.
