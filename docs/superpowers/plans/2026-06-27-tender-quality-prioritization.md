# Tender Quality Prioritization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a priority layer that separates collected tenders into hot, review, wide, and excluded groups while preserving maximum candidate coverage.

**Architecture:** Extend `TenderRecord` with `review_priority`, persist and export it, then set it in `evaluate_tender()`. Keep existing `filter_status` and `match_confidence` behavior as the compatibility layer, and use priority for sorting/report grouping.

**Tech Stack:** Python 3, dataclasses, SQLite, pytest, openpyxl.

## Global Constraints

- Keep collecting broad candidate tenders; do not silently drop uncertain but potentially useful records.
- Exclude clear non-target topics: fuel, GSM, construction, road/building repair, medicines, pharma, lab/clinical supplies, medical cartridges, dialysis, and medical consumables.
- Use TDD: write each behavior test first, verify it fails, then implement minimal code.
- Preserve current command-line behavior: `python -m tender_parser run` still produces Excel, `latest.json`, `new_tenders.json`, and `run_report.json`.
- Do not add new external dependencies.
- Do not add new tender sources in this task.

---

### Task 1: Add Review Priority To Model, Storage, And JSON

**Files:**
- Modify: `tender_parser/models.py`
- Modify: `tender_parser/storage.py`
- Modify: `tender_parser/exporters/json_exporter.py`
- Test: `tests/test_exporters.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `ReviewPriority = Literal["hot", "review", "wide", "excluded"]`
- Produces: `TenderRecord.review_priority: ReviewPriority | None`
- Produces JSON field: `"review_priority": tender.review_priority`
- Produces SQLite column: `review_priority TEXT`

- [ ] **Step 1: Add failing JSON export assertion**

Modify `tests/test_exporters.py` so `make_tender()` sets a priority and `test_export_json_writes_matched_tenders()` expects it:

```python
def make_tender(status: str) -> TenderRecord:
    return TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        customer="Заказчик",
        region="Республика Крым",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status=status,
        review_priority="hot" if status == "matched" else "review",
        category="Компьютерная техника и периферия" if status != "excluded" else None,
        include_reason="ok" if status != "excluded" else "",
        exclude_reason="" if status == "matched" else "регион не найден",
        match_confidence="точное" if status == "matched" else "ручная проверка",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )
```

Then add:

```python
assert data["items"][0]["review_priority"] == "hot"
```

- [ ] **Step 2: Run export test and verify it fails**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_exporters.py::test_export_json_writes_matched_tenders -q
```

Expected: failure because `review_priority` is not exported yet, or `TenderRecord` does not accept the field.

- [ ] **Step 3: Add failing storage persistence assertion**

Modify `tests/test_storage.py` so a stored and loaded record keeps `review_priority="hot"`:

```python
assert loaded[0].review_priority == "hot"
```

Also update the legacy schema migration test to assert that `review_priority` is added to old databases:

```python
columns = {row["name"] for row in conn.execute("PRAGMA table_info(tenders)").fetchall()}
assert "review_priority" in columns
```

- [ ] **Step 4: Run storage tests and verify they fail**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_storage.py -q
```

Expected: failure because the schema and dataclass do not have `review_priority`.

- [ ] **Step 5: Implement model field**

In `tender_parser/models.py`, add:

```python
ReviewPriority = Literal["hot", "review", "wide", "excluded"]
```

Then add this field to `TenderRecord` after `match_confidence`:

```python
review_priority: ReviewPriority | None = None
```

- [ ] **Step 6: Implement storage migration and persistence**

In `tender_parser/storage.py`:

- add `review_priority TEXT` to the `CREATE TABLE` statement;
- add an `ALTER TABLE tenders ADD COLUMN review_priority TEXT` migration if the column is missing;
- include `review_priority` in `INSERT`, `VALUES`, `ON CONFLICT DO UPDATE`, and the argument tuple;
- set `review_priority=row["review_priority"]` in `_row_to_record()`.

- [ ] **Step 7: Implement JSON export field**

In `_to_dict()` in `tender_parser/exporters/json_exporter.py`, add:

```python
"review_priority": tender.review_priority,
```

- [ ] **Step 8: Verify Task 1 tests pass**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_exporters.py tests/test_storage.py -q
```

Expected: both test files pass.

- [ ] **Step 9: Commit Task 1**

Run:

```powershell
git add tender_parser/models.py tender_parser/storage.py tender_parser/exporters/json_exporter.py tests/test_exporters.py tests/test_storage.py
git commit -m "Add tender review priority field"
```

---

### Task 2: Set Priority In Tender Evaluation

**Files:**
- Modify: `tender_parser/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: `TenderRecord.review_priority`
- Produces: `_exclude()` always sets `review_priority="excluded"`
- Produces: matched exact records get `review_priority="hot"`
- Produces: plausible missing-data records get `review_priority="review"`
- Produces: low-confidence broad-source records get `review_priority="wide"`

- [ ] **Step 1: Add failing priority tests**

Add these tests to `tests/test_filters.py`:

```python
def test_evaluate_tender_marks_exact_match_hot() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.review_priority == "hot"


def test_evaluate_tender_marks_missing_deadline_as_review_priority() -> None:
    result = evaluate_tender(make_tender(deadline=None), now=NOW)

    assert result.filter_status == "review"
    assert result.match_confidence == "вероятное"
    assert result.review_priority == "review"


def test_evaluate_tender_marks_b2b_missing_region_and_price_as_wide() -> None:
    result = evaluate_tender(
        make_tender(
            source="b2b-center",
            title="Поставка кондиционеров",
            region=None,
            price=None,
            raw_text="Поставка кондиционеров",
        ),
        now=NOW,
    )

    assert result.filter_status == "review"
    assert result.match_confidence == "ручная проверка"
    assert result.review_priority == "wide"
```

- [ ] **Step 2: Add failing exclusion quality tests**

Add:

```python
def test_evaluate_tender_excludes_lab_consumables_even_with_office_word() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка расходных материалов для клинико-диагностической лаборатории",
            raw_text="Поставка ручек-скарификаторов и расходных материалов для лаборатории в Севастополь",
            region="Севастополь",
            price=500_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.review_priority == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_does_not_promote_generic_consumables_to_hot() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка расходных материалов",
            raw_text="Поставка расходных материалов в Республику Крым",
            region="Республика Крым",
            price=120_000.0,
        ),
        now=NOW,
    )

    assert result.review_priority != "hot"
```

- [ ] **Step 3: Run new filter tests and verify they fail**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_filters.py -q
```

Expected: failures for missing `review_priority` behavior and lab/generic consumable handling.

- [ ] **Step 4: Implement priority assignment**

In `tender_parser/filters.py`:

- `_exclude()` should set `review_priority="excluded"`;
- `_review()` should accept `priority: ReviewPriority = "review"` and set `review_priority=priority`;
- exact matched records should set `review_priority="hot"`;
- if missing data includes both missing region and missing price, set priority to `"wide"`;
- if `tender.source == "b2b-center"` and region or price is missing, set priority to `"wide"`;
- otherwise missing deadline or missing price alone remains priority `"review"`.

- [ ] **Step 5: Implement stricter stop terms for medical/lab false positives**

In `tender_parser/config.py`, add stop terms for:

```python
"клинико-диагностическая лаборатория",
"лаборатория",
"скарификатор",
"медицинские расходные материалы",
"изделия медицинского назначения",
"медицинские изделия",
```

Keep the existing stop list behavior so these terms exclude records before category matching.

- [ ] **Step 6: Verify Task 2 tests pass**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_filters.py -q
```

Expected: all filter tests pass.

- [ ] **Step 7: Commit Task 2**

Run:

```powershell
git add tender_parser/filters.py tender_parser/config.py tests/test_filters.py
git commit -m "Prioritize tender review quality"
```

---

### Task 3: Export Priority-Based Excel Sheets And Sorting

**Files:**
- Modify: `tender_parser/exporters/excel.py`
- Modify: `tender_parser/cli.py`
- Test: `tests/test_exporters.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `sort_for_review(tenders: list[TenderRecord]) -> list[TenderRecord]`
- Produces Excel sheets: `Новые`, `Горячие`, `На проверку`, `Широкий хвост`, `Отсеянные`
- Consumes: `review_priority`

- [ ] **Step 1: Add failing Excel sheet test**

Modify `tests/test_exporters.py` to expect the new sheet names:

```python
assert workbook.sheetnames == ["Горячие", "На проверку", "Широкий хвост", "Отсеянные"]
```

In the new-sheet test, expect:

```python
assert workbook.sheetnames[0] == "Новые"
```

Add an assertion that the priority column is present:

```python
assert workbook["Горячие"]["D1"].value == "приоритет"
assert workbook["Горячие"]["D2"].value == "hot"
```

- [ ] **Step 2: Run Excel test and verify it fails**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_exporters.py -q
```

Expected: failure because current sheets use `Подходящие` and no `Широкий хвост` sheet.

- [ ] **Step 3: Implement Excel grouping and priority column**

In `tender_parser/exporters/excel.py`:

- add `"приоритет"` after `"уверенность"` in `HEADERS`;
- add `tender.review_priority or ""` in `_append_rows()`;
- change `export_excel()` signature to:

```python
def export_excel(
    hot: list[TenderRecord],
    review: list[TenderRecord],
    wide: list[TenderRecord],
    excluded: list[TenderRecord],
    output_path: Path,
    *,
    new_tenders: list[TenderRecord] | None = None,
) -> Path:
```

- create sheets in this order: `Горячие`, `На проверку`, `Широкий хвост`, `Отсеянные`;
- keep `Новые` inserted at index `0` when provided.

- [ ] **Step 4: Add sorting helper tests**

Add to `tests/test_exporters.py`:

```python
from tender_parser.exporters.excel import sort_for_review


def test_sort_for_review_orders_by_priority_deadline_price_and_discovery() -> None:
    hot_late = make_tender("matched")
    hot_late = replace(hot_late, tender_number="2", deadline=datetime(2026, 5, 30), price=100_000.0)
    hot_soon = replace(make_tender("matched"), tender_number="1", deadline=datetime(2026, 5, 20), price=40_000.0)
    wide = replace(make_tender("review"), tender_number="3", review_priority="wide", deadline=datetime(2026, 5, 19), price=1_000_000.0)

    result = sort_for_review([wide, hot_late, hot_soon])

    assert [item.tender_number for item in result] == ["1", "2", "3"]
```

Add `from dataclasses import replace` at the top of the test file.

- [ ] **Step 5: Implement `sort_for_review()`**

In `tender_parser/exporters/excel.py`, add:

```python
PRIORITY_ORDER = {"hot": 0, "review": 1, "wide": 2, "excluded": 3, None: 4}


def sort_for_review(tenders: list[TenderRecord]) -> list[TenderRecord]:
    return sorted(
        tenders,
        key=lambda tender: (
            PRIORITY_ORDER.get(tender.review_priority, 4),
            tender.deadline is None,
            tender.deadline or datetime.max,
            tender.price is None,
            -(tender.price or 0),
            -(tender.discovered_at.timestamp() if tender.discovered_at else 0),
            tender.title,
        ),
    )
```

- [ ] **Step 6: Update CLI grouping**

In `tender_parser/cli.py`, replace current grouping with:

```python
hot = [tender for tender in evaluated if tender.review_priority == "hot"]
review = [tender for tender in evaluated if tender.review_priority == "review"]
wide = [tender for tender in evaluated if tender.review_priority == "wide"]
excluded = [tender for tender in evaluated if tender.review_priority == "excluded"]
actionable = sort_for_review(hot + review + wide)
new_actionable = sort_for_review(
    [tender for tender in first_seen if tender.review_priority in {"hot", "review", "wide"}]
)
```

Import `sort_for_review` from `tender_parser.exporters.excel`.

Update `export_excel()` call to pass `hot, review, wide, excluded`.

Update printed counters to show:

```python
print(f"Горячие: {len(hot)}")
print(f"На проверку: {len(review)}")
print(f"Широкий хвост: {len(wide)}")
```

- [ ] **Step 7: Verify Task 3 tests pass**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest tests/test_exporters.py tests/test_cli.py -q
```

Expected: exporter and CLI tests pass.

- [ ] **Step 8: Commit Task 3**

Run:

```powershell
git add tender_parser/exporters/excel.py tender_parser/cli.py tests/test_exporters.py tests/test_cli.py
git commit -m "Export prioritized tender review sheets"
```

---

### Task 4: Documentation, Full Verification, And Live Run

**Files:**
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`
- Modify: this plan file

**Interfaces:**
- Documents `review_priority`
- Documents new Excel sheet layout
- Documents next step: EAT/EIS tokens after quality layer

- [ ] **Step 1: Update README**

Update the report section to say the Excel workbook contains:

```markdown
- `Новые` - all new actionable tenders, ordered by priority.
- `Горячие` - strong matches ready for first review.
- `На проверку` - plausible tenders with one missing field.
- `Широкий хвост` - low-confidence potential tenders for manual sweep.
- `Отсеянные` - excluded records with reasons.
```

Add a short explanation that JSON exports include `review_priority`.

- [ ] **Step 2: Update memory and handoff**

In `docs/MEMORY.md` and `docs/HANDOFF.md`, add:

```markdown
- Quality layer added: `review_priority` splits candidates into `hot`, `review`, `wide`, and `excluded`.
- The next integration step remains EAT token setup and then the official EIS data channel.
```

- [ ] **Step 3: Run full test suite**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run live parser**

Run:

```powershell
& "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -m tender_parser run
```

Expected: command exits `0`, creates current-date Excel and JSON exports.

- [ ] **Step 5: Inspect exported counts and workbook sheets**

Run:

```powershell
@'
import json
from collections import Counter
from pathlib import Path
from openpyxl import load_workbook

base = Path.cwd()
latest = json.loads((base / "exports" / "latest.json").read_text(encoding="utf-8"))["items"]
print("latest_count", len(latest))
print("priority_counts", dict(Counter(item.get("review_priority") for item in latest)))
workbook = load_workbook(base / "exports" / "tenders_2026-06-27.xlsx", read_only=True)
print("sheets", workbook.sheetnames)
'@ | & "C:\Users\user\Documents\GitHub\Codex\Парсинг_тендеры\.venv\Scripts\python.exe" -
```

Expected: `review_priority` counts are present and sheets include `Горячие`, `На проверку`, `Широкий хвост`, `Отсеянные`.

- [ ] **Step 6: Commit Task 4**

Run:

```powershell
git add README.md docs/MEMORY.md docs/HANDOFF.md docs/superpowers/plans/2026-06-27-tender-quality-prioritization.md
git commit -m "Document prioritized tender review flow"
```

- [ ] **Step 7: Finish branch**

Use `superpowers:finishing-a-development-branch`:

1. verify tests again;
2. merge `codex/tender-quality-prioritization` back into `codex/rts-tender-parser`;
3. run tests in the main checkout;
4. run the live parser in the main checkout;
5. push `codex/rts-tender-parser` to `target`.

