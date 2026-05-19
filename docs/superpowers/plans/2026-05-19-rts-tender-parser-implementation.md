# RTS Tender Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows one-click parser for public RTS-Tender procedures that saves history in SQLite and exports relevant active tenders to Excel and JSON.

**Architecture:** Use a small Python package with clear modules: config and models, filtering, SQLite storage, exporters, RTS public source parsing, and CLI orchestration. The first live source is the public Rosatom section of RTS-Tender, because it exposes server-rendered HTML at `/market/` with query parameters and pagination.

**Tech Stack:** Python 3.11+, `requests`, `beautifulsoup4`, `openpyxl`, `pytest`, `sqlite3` from the standard library, Windows batch launcher.

---

## Scope Check

The approved spec describes one cohesive first version: public RTS parsing, filters, storage, exports, and one-button launch. It does not need to be split into multiple implementation plans.

## File Structure

- Create `requirements.txt`: runtime and test dependencies.
- Create `Запустить_парсер.bat`: Windows one-click launcher.
- Create `tender_parser/__init__.py`: package marker and version.
- Create `tender_parser/__main__.py`: enables `python -m tender_parser`.
- Create `tender_parser/cli.py`: command-line entry point and run orchestration.
- Create `tender_parser/config.py`: regions, keywords, stop terms, source settings.
- Create `tender_parser/models.py`: dataclasses shared between modules.
- Create `tender_parser/text.py`: text normalization, price parsing, date parsing.
- Create `tender_parser/filters.py`: business filtering logic.
- Create `tender_parser/storage.py`: SQLite schema and upsert/fetch functions.
- Create `tender_parser/exporters/__init__.py`: exporter package marker.
- Create `tender_parser/exporters/excel.py`: Excel export.
- Create `tender_parser/exporters/json_exporter.py`: CRM-friendly JSON export.
- Create `tender_parser/sources/__init__.py`: source package marker.
- Create `tender_parser/sources/rts.py`: public RTS-Tender HTML fetching and parsing.
- Create `tests/fixtures/rts_market_sample.html`: small stable HTML fixture.
- Create `tests/test_text.py`: parser helper tests.
- Create `tests/test_filters.py`: business rule tests.
- Create `tests/test_storage.py`: SQLite tests.
- Create `tests/test_exporters.py`: Excel and JSON tests.
- Create `tests/test_rts_source.py`: RTS HTML parsing tests.
- Create `tests/test_cli.py`: smoke test for a dry run.

## Source Notes

Verified public RTS-Rosatom market endpoint:

- Base page: `https://www.rosatom.rts-tender.ru/market/`
- Keyword search: `https://www.rosatom.rts-tender.ru/market/?searching=1&f_keyword=<urlencoded keyword>&price_start=30000`
- Pagination: append `from=20`, `from=40`, etc.
- Results table selector: `table.search-results tbody tr`
- Result link selector: `a.search-results-title`
- Row columns: procedure, organizer, price, published, active until.

The first implementation should fetch at most the first two pages per keyword by default. This is enough to validate behavior without being noisy.

---

### Task 1: Project Scaffold And Launcher

**Files:**
- Create: `requirements.txt`
- Create: `Запустить_парсер.bat`
- Create: `tender_parser/__init__.py`
- Create: `tender_parser/__main__.py`
- Create: `tender_parser/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI smoke test**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from tender_parser.cli import run


def test_run_dry_mode_creates_export_dirs(tmp_path: Path) -> None:
    result = run(["--dry-run", "--base-dir", str(tmp_path)])

    assert result == 0
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "exports").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tender_parser'`.

- [ ] **Step 3: Add dependencies**

Create `requirements.txt`:

```text
beautifulsoup4==4.12.3
openpyxl==3.1.5
pytest==8.3.4
requests==2.32.3
```

- [ ] **Step 4: Create package entry points**

Create `tender_parser/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `tender_parser/__main__.py`:

```python
from tender_parser.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `tender_parser/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
    parser.add_argument("--base-dir", default=".", help="Project directory for data and exports")
    parser.add_argument("--dry-run", action="store_true", help="Create directories and exit")
    return parser


def ensure_dirs(base_dir: Path) -> tuple[Path, Path]:
    data_dir = base_dir / "data"
    exports_dir = base_dir / "exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, exports_dir


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    ensure_dirs(base_dir)
    if args.dry_run:
        return 0
    print("Парсер еще не подключен к источникам. Следующий шаг плана добавит логику.")
    return 0


def main() -> int:
    return run()
```

- [ ] **Step 5: Create Windows launcher**

Create `Запустить_парсер.bat`:

```bat
@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m tender_parser run

if exist "exports" (
    start "" "exports"
)

pause
```

- [ ] **Step 6: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add requirements.txt Запустить_парсер.bat tender_parser tests/test_cli.py
git commit -m "feat: scaffold parser cli"
```

---

### Task 2: Models, Config, And Text Helpers

**Files:**
- Create: `tender_parser/models.py`
- Create: `tender_parser/config.py`
- Create: `tender_parser/text.py`
- Create: `tests/test_text.py`

- [ ] **Step 1: Write failing text helper tests**

Create `tests/test_text.py`:

```python
from datetime import datetime

from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


def test_normalize_text_lowercases_and_collapses_spaces() -> None:
    assert normalize_text("  МФУ\nПринтер   ") == "мфу принтер"


def test_parse_price_rub_accepts_russian_format() -> None:
    assert parse_price_rub("154 200,50 ₽") == 154200.50


def test_parse_price_rub_returns_none_for_missing_price() -> None:
    assert parse_price_rub("Без указания цены") is None


def test_parse_deadline_reads_russian_datetime() -> None:
    assert parse_deadline("29.05.2026 10:00") == datetime(2026, 5, 29, 10, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_text.py -v
```

Expected: FAIL with missing `tender_parser.text`.

- [ ] **Step 3: Create shared models**

Create `tender_parser/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


FilterStatus = Literal["matched", "excluded"]


@dataclass(frozen=True)
class TenderRecord:
    title: str
    url: str
    source: str
    tender_number: str | None = None
    customer: str | None = None
    region: str | None = None
    price: float | None = None
    deadline: datetime | None = None
    status: str | None = None
    published_at: datetime | None = None
    discovered_at: datetime | None = None
    raw_text: str = ""
    category: str | None = None
    include_reason: str = ""
    exclude_reason: str = ""
    filter_status: FilterStatus = "excluded"
    matched_terms: list[str] = field(default_factory=list)

    @property
    def unique_key(self) -> str:
        if self.tender_number:
            return f"{self.source}:{self.tender_number}"
        return f"{self.source}:{self.url.split('#', 1)[0].rstrip('/')}"
```

- [ ] **Step 4: Create config dictionaries**

Create `tender_parser/config.py`:

```python
MIN_PRICE_RUB = 30_000

REGION_TERMS = [
    "Симферополь",
    "Севастополь",
    "Республика Крым",
    "Крым",
    "Запорожская область",
    "Херсонская область",
]

CATEGORY_KEYWORDS = {
    "Компьютерная техника и периферия": [
        "компьютер",
        "ноутбук",
        "моноблок",
        "системный блок",
        "сервер",
        "монитор",
        "клавиатура",
        "мышь",
        "МФУ",
        "многофункциональное устройство",
        "принтер",
        "сканер",
        "картридж",
        "тонер",
        "коммутатор",
        "маршрутизатор",
        "роутер",
        "ИБП",
    ],
    "Климатическая техника": [
        "кондиционер",
        "сплит-система",
        "вентиляция",
        "очистка кондиционеров",
        "обслуживание кондиционеров",
        "монтаж кондиционера",
    ],
    "Бытовая техника": [
        "холодильник",
        "стиральная машина",
        "микроволновая печь",
        "чайник",
        "пылесос",
        "кулер",
        "водонагреватель",
    ],
    "Канцелярия и офис": [
        "канцелярские товары",
        "бумага",
        "папка",
        "ручка",
        "картон",
        "офисные принадлежности",
    ],
    "Электротехника и оборудование": [
        "кабель",
        "провод",
        "розетка",
        "выключатель",
        "светильник",
        "лампа",
        "удлинитель",
        "щит",
        "сейф",
        "стеллаж",
        "шкаф",
        "ящик",
    ],
}

STOP_TERMS = [
    "ГСМ",
    "топливо",
    "дизель",
    "бензин",
    "капитальный ремонт",
    "капстроительство",
    "строительство",
    "ремонт здания",
    "ремонт дороги",
    "автомобильная дорога",
    "асфальт",
    "проектно-сметная документация",
    "лекарства",
    "лекарственные препараты",
    "лекарственный препарат",
    "медикаменты",
    "медицинские препараты",
    "фармацевтическая продукция",
]

RTS_MARKET_BASE_URL = "https://www.rosatom.rts-tender.ru/market/"
RTS_MAX_PAGES_PER_KEYWORD = 2
HTTP_TIMEOUT_SECONDS = 25
```

- [ ] **Step 5: Create text helper implementation**

Create `tender_parser/text.py`:

```python
from __future__ import annotations

import re
from datetime import datetime


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip().lower()


def parse_price_rub(value: str | None) -> float | None:
    text = normalize_text(value)
    if not text or "без указания цены" in text or "$" in text or "usd" in text:
        return None
    if "₽" not in text and "руб" not in text:
        return None
    cleaned = re.sub(r"[^\d,\.]", "", text)
    if not cleaned:
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)


def parse_deadline(value: str | None) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_text.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add tender_parser/models.py tender_parser/config.py tender_parser/text.py tests/test_text.py
git commit -m "feat: add parser models and text helpers"
```

---

### Task 3: Business Filters

**Files:**
- Create: `tender_parser/filters.py`
- Create: `tests/test_filters.py`

- [ ] **Step 1: Write failing filter tests**

Create `tests/test_filters.py`:

```python
from datetime import datetime

from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord


NOW = datetime(2026, 5, 19, 12, 0)


def make_tender(**overrides: object) -> TenderRecord:
    data = {
        "title": "Поставка МФУ в Республику Крым",
        "url": "https://example.test/tender-1/",
        "source": "test",
        "tender_number": "1",
        "customer": "Заказчик",
        "region": "Республика Крым",
        "price": 45_000.0,
        "deadline": datetime(2026, 5, 25, 10, 0),
        "raw_text": "Поставка МФУ в Республику Крым",
    }
    data.update(overrides)
    return TenderRecord(**data)


def test_evaluate_tender_matches_region_category_price_and_deadline() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.category == "Компьютерная техника и периферия"
    assert "регион: республика крым" in result.include_reason.lower()
    assert "мфу" in result.include_reason.lower()


def test_evaluate_tender_excludes_stop_terms() -> None:
    result = evaluate_tender(make_tender(title="Поставка лекарственных препаратов МФУ"), now=NOW)

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason
    assert "лекарственные препараты" in result.exclude_reason


def test_evaluate_tender_excludes_low_price() -> None:
    result = evaluate_tender(make_tender(price=29_999.0), now=NOW)

    assert result.filter_status == "excluded"
    assert "меньше 30000" in result.exclude_reason


def test_evaluate_tender_excludes_expired_deadline() -> None:
    result = evaluate_tender(make_tender(deadline=datetime(2026, 5, 18, 23, 59)), now=NOW)

    assert result.filter_status == "excluded"
    assert "срок подачи истек" in result.exclude_reason


def test_evaluate_tender_excludes_missing_region() -> None:
    result = evaluate_tender(make_tender(region="Москва", raw_text="Поставка МФУ"), now=NOW)

    assert result.filter_status == "excluded"
    assert "регион не найден" in result.exclude_reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_filters.py -v
```

Expected: FAIL with missing `tender_parser.filters`.

- [ ] **Step 3: Implement filters**

Create `tender_parser/filters.py`:

```python
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from tender_parser.config import CATEGORY_KEYWORDS, MIN_PRICE_RUB, REGION_TERMS, STOP_TERMS
from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text


def _first_matching_term(text: str, terms: list[str]) -> str | None:
    for term in terms:
        normalized = normalize_text(term)
        if normalized and normalized in text:
            return normalized
    return None


def _matching_category(text: str) -> tuple[str | None, list[str]]:
    for category, terms in CATEGORY_KEYWORDS.items():
        matches = [normalize_text(term) for term in terms if normalize_text(term) in text]
        if matches:
            return category, matches
    return None, []


def evaluate_tender(tender: TenderRecord, now: datetime | None = None) -> TenderRecord:
    current = now or datetime.now()
    searchable = normalize_text(" ".join([tender.title, tender.region or "", tender.customer or "", tender.raw_text]))

    stop_term = _first_matching_term(searchable, STOP_TERMS)
    if stop_term:
        return replace(
            tender,
            filter_status="excluded",
            exclude_reason=f"стоп-тема: {stop_term}",
        )

    if tender.price is None or tender.price < MIN_PRICE_RUB:
        return replace(
            tender,
            filter_status="excluded",
            exclude_reason=f"сумма меньше {MIN_PRICE_RUB} или не указана",
        )

    if tender.deadline is None or tender.deadline <= current:
        return replace(
            tender,
            filter_status="excluded",
            exclude_reason="срок подачи истек или не указан",
        )

    region = _first_matching_term(searchable, REGION_TERMS)
    if not region:
        return replace(tender, filter_status="excluded", exclude_reason="регион не найден")

    category, terms = _matching_category(searchable)
    if not category:
        return replace(tender, filter_status="excluded", exclude_reason="категория интереса не найдена")

    include_reason = (
        f"регион: {region}; категория: {category}; "
        f"ключевые слова: {', '.join(terms)}; сумма: {tender.price:.2f}; срок подачи активен"
    )
    return replace(
        tender,
        filter_status="matched",
        category=category,
        include_reason=include_reason,
        exclude_reason="",
        matched_terms=terms,
    )
```

- [ ] **Step 4: Run filter tests**

Run:

```powershell
python -m pytest tests/test_filters.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add tender_parser/filters.py tests/test_filters.py
git commit -m "feat: add tender filters"
```

---

### Task 4: SQLite Storage

**Files:**
- Create: `tender_parser/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_storage.py`:

```python
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.storage import TenderStorage


def test_storage_upserts_without_duplicates(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status="matched",
        include_reason="ok",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )

    storage.upsert_many([tender])
    storage.upsert_many([tender])

    rows = storage.fetch_by_status("matched")
    assert len(rows) == 1
    assert rows[0].title == "Поставка МФУ"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_storage.py -v
```

Expected: FAIL with missing `tender_parser.storage`.

- [ ] **Step 3: Implement SQLite storage**

Create `tender_parser/storage.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _str_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class TenderStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tenders (
                    unique_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    tender_number TEXT,
                    customer TEXT,
                    region TEXT,
                    price REAL,
                    deadline TEXT,
                    status TEXT,
                    published_at TEXT,
                    discovered_at TEXT,
                    last_seen_at TEXT,
                    raw_text TEXT,
                    category TEXT,
                    include_reason TEXT,
                    exclude_reason TEXT,
                    filter_status TEXT NOT NULL
                )
                """
            )

    def upsert_many(self, tenders: list[TenderRecord]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            for tender in tenders:
                discovered = _dt_to_str(tender.discovered_at) or now
                conn.execute(
                    """
                    INSERT INTO tenders (
                        unique_key, title, url, source, tender_number, customer, region,
                        price, deadline, status, published_at, discovered_at, last_seen_at,
                        raw_text, category, include_reason, exclude_reason, filter_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(unique_key) DO UPDATE SET
                        title=excluded.title,
                        url=excluded.url,
                        customer=excluded.customer,
                        region=excluded.region,
                        price=excluded.price,
                        deadline=excluded.deadline,
                        status=excluded.status,
                        published_at=excluded.published_at,
                        last_seen_at=excluded.last_seen_at,
                        raw_text=excluded.raw_text,
                        category=excluded.category,
                        include_reason=excluded.include_reason,
                        exclude_reason=excluded.exclude_reason,
                        filter_status=excluded.filter_status
                    """,
                    (
                        tender.unique_key,
                        tender.title,
                        tender.url,
                        tender.source,
                        tender.tender_number,
                        tender.customer,
                        tender.region,
                        tender.price,
                        _dt_to_str(tender.deadline),
                        tender.status,
                        _dt_to_str(tender.published_at),
                        discovered,
                        now,
                        tender.raw_text,
                        tender.category,
                        tender.include_reason,
                        tender.exclude_reason,
                        tender.filter_status,
                    ),
                )

    def fetch_by_status(self, status: str) -> list[TenderRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenders WHERE filter_status = ? ORDER BY deadline ASC, title ASC",
                (status,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> TenderRecord:
        return TenderRecord(
            title=row["title"],
            url=row["url"],
            source=row["source"],
            tender_number=row["tender_number"],
            customer=row["customer"],
            region=row["region"],
            price=row["price"],
            deadline=_str_to_dt(row["deadline"]),
            status=row["status"],
            published_at=_str_to_dt(row["published_at"]),
            discovered_at=_str_to_dt(row["discovered_at"]),
            raw_text=row["raw_text"] or "",
            category=row["category"],
            include_reason=row["include_reason"] or "",
            exclude_reason=row["exclude_reason"] or "",
            filter_status=row["filter_status"],
        )
```

- [ ] **Step 4: Run storage tests**

Run:

```powershell
python -m pytest tests/test_storage.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add tender_parser/storage.py tests/test_storage.py
git commit -m "feat: add sqlite tender storage"
```

---

### Task 5: Excel And JSON Exporters

**Files:**
- Create: `tender_parser/exporters/__init__.py`
- Create: `tender_parser/exporters/excel.py`
- Create: `tender_parser/exporters/json_exporter.py`
- Create: `tests/test_exporters.py`

- [ ] **Step 1: Write failing exporter tests**

Create `tests/test_exporters.py`:

```python
import json
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from tender_parser.exporters.excel import export_excel
from tender_parser.exporters.json_exporter import export_json
from tender_parser.models import TenderRecord


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
        category="Компьютерная техника и периферия" if status == "matched" else None,
        include_reason="ok" if status == "matched" else "",
        exclude_reason="" if status == "matched" else "регион не найден",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )


def test_export_excel_creates_expected_sheets(tmp_path: Path) -> None:
    output = tmp_path / "tenders.xlsx"
    export_excel([make_tender("matched")], [make_tender("excluded")], output)

    workbook = load_workbook(output)
    assert workbook.sheetnames == ["Подходящие", "Отсеянные"]
    assert workbook["Подходящие"]["C2"].value == "Поставка МФУ"


def test_export_json_writes_matched_tenders(tmp_path: Path) -> None:
    output = tmp_path / "latest.json"
    export_json([make_tender("matched")], output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["items"][0]["title"] == "Поставка МФУ"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_exporters.py -v
```

Expected: FAIL with missing exporter modules.

- [ ] **Step 3: Implement Excel exporter**

Create `tender_parser/exporters/__init__.py`:

```python
```

Create `tender_parser/exporters/excel.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from tender_parser.models import TenderRecord


HEADERS = [
    "дата_обнаружения",
    "категория",
    "название",
    "номер",
    "заказчик",
    "регион",
    "цена",
    "срок_подачи",
    "статус",
    "ссылка",
    "причина_включения",
    "причина_исключения",
    "источник",
]


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _append_rows(sheet: Worksheet, tenders: list[TenderRecord]) -> None:
    sheet.append(HEADERS)
    for tender in tenders:
        sheet.append(
            [
                _format_dt(tender.discovered_at),
                tender.category or "",
                tender.title,
                tender.tender_number or "",
                tender.customer or "",
                tender.region or "",
                tender.price,
                _format_dt(tender.deadline),
                tender.status or "",
                tender.url,
                tender.include_reason,
                tender.exclude_reason,
                tender.source,
            ]
        )
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(max(len(str(cell.value or "")) for cell in column) + 2, 60)


def export_excel(matched: list[TenderRecord], excluded: list[TenderRecord], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    matched_sheet = workbook.active
    matched_sheet.title = "Подходящие"
    _append_rows(matched_sheet, matched)
    excluded_sheet = workbook.create_sheet("Отсеянные")
    _append_rows(excluded_sheet, excluded)
    workbook.save(output_path)
    return output_path
```

- [ ] **Step 4: Implement JSON exporter**

Create `tender_parser/exporters/json_exporter.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tender_parser.models import TenderRecord


def _format_dt(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _to_dict(tender: TenderRecord) -> dict[str, object]:
    return {
        "title": tender.title,
        "url": tender.url,
        "source": tender.source,
        "tender_number": tender.tender_number,
        "customer": tender.customer,
        "region": tender.region,
        "price": tender.price,
        "deadline": _format_dt(tender.deadline),
        "status": tender.status,
        "published_at": _format_dt(tender.published_at),
        "discovered_at": _format_dt(tender.discovered_at),
        "category": tender.category,
        "include_reason": tender.include_reason,
    }


def export_json(matched: list[TenderRecord], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(matched),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": [_to_dict(tender) for tender in matched],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
```

- [ ] **Step 5: Run exporter tests**

Run:

```powershell
python -m pytest tests/test_exporters.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add tender_parser/exporters tests/test_exporters.py
git commit -m "feat: add tender exports"
```

---

### Task 6: RTS Public Source Parser

**Files:**
- Create: `tender_parser/sources/__init__.py`
- Create: `tender_parser/sources/rts.py`
- Create: `tests/fixtures/rts_market_sample.html`
- Create: `tests/test_rts_source.py`

- [ ] **Step 1: Write failing source parser tests**

Create `tests/fixtures/rts_market_sample.html`:

```html
<html>
  <body>
    <table class="table search-results">
      <tbody>
        <tr>
          <td>
            <a href="https://www.rosatom.rts-tender.ru/market/postavka-mfu/tender-4455001/#btid=2"
               class="search-results-title">
              Запрос предложений № 4455001
              <div class="search-results-title-desc">Поставка МФУ в Республику Крым</div>
            </a>
          </td>
          <td><a>АО "ТЕСТ"</a></td>
          <td>45 000,00&nbsp;<span>₽</span></td>
          <td class="nowrap">19.05.2026 12:00</td>
          <td class="nowrap">25.05.2026 10:00</td>
          <td></td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
```

Create `tests/test_rts_source.py`:

```python
from pathlib import Path

from tender_parser.sources.rts import parse_market_page


def test_parse_market_page_extracts_table_rows() -> None:
    html = Path("tests/fixtures/rts_market_sample.html").read_text(encoding="utf-8")
    tenders = parse_market_page(html, source_url="https://www.rosatom.rts-tender.ru/market/")

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.tender_number == "4455001"
    assert tender.title == "Запрос предложений № 4455001 Поставка МФУ в Республику Крым"
    assert tender.customer == 'АО "ТЕСТ"'
    assert tender.price == 45_000.0
    assert tender.deadline.year == 2026
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_rts_source.py -v
```

Expected: FAIL with missing `tender_parser.sources.rts`.

- [ ] **Step 3: Implement RTS parser**

Create `tender_parser/sources/__init__.py`:

```python
```

Create `tender_parser/sources/rts.py`:

```python
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from tender_parser.config import HTTP_TIMEOUT_SECONDS, MIN_PRICE_RUB, RTS_MARKET_BASE_URL, RTS_MAX_PAGES_PER_KEYWORD
from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RTS-Tender-Parser/0.1"


def clean_url(url: str) -> str:
    return url.split("#", 1)[0]


def build_search_url(keyword: str, page_index: int = 0) -> str:
    params = {
        "searching": "1",
        "f_keyword": keyword,
        "price_start": str(MIN_PRICE_RUB),
    }
    if page_index:
        params["from"] = str(page_index * 20)
    return f"{RTS_MARKET_BASE_URL}?{urlencode(params)}"


def parse_market_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table.search-results tbody tr")
    tenders: list[TenderRecord] = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        link = cells[0].select_one("a.search-results-title")
        if link is None or not link.get("href"):
            continue

        title = normalize_text(link.get_text(" "))
        display_title = " ".join(part.strip() for part in link.get_text(" ").split() if part.strip())
        number_match = re.search(r"№\s*(\d+)", display_title)
        customer = cells[1].get_text(" ", strip=True)
        price_text = cells[2].get_text(" ", strip=True)
        published_text = cells[3].get_text(" ", strip=True)
        deadline_text = cells[4].get_text(" ", strip=True)
        raw_text = row.get_text(" ", strip=True)

        tenders.append(
            TenderRecord(
                title=display_title,
                url=clean_url(str(link["href"])),
                source="rts-rosatom",
                tender_number=number_match.group(1) if number_match else None,
                customer=customer or None,
                price=parse_price_rub(price_text),
                deadline=parse_deadline(deadline_text),
                status="Актуально",
                published_at=parse_deadline(published_text),
                discovered_at=datetime.now(),
                raw_text=raw_text,
                region=_extract_region(raw_text),
            )
        )
    return tenders


def _extract_region(text: str) -> str | None:
    normalized = normalize_text(text)
    for region in ("Симферополь", "Севастополь", "Республика Крым", "Крым", "Запорожская область", "Херсонская область"):
        if normalize_text(region) in normalized:
            return region
    return None


class RtsPublicSource:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_keyword(self, keyword: str, max_pages: int = RTS_MAX_PAGES_PER_KEYWORD) -> list[TenderRecord]:
        tenders: list[TenderRecord] = []
        for page_index in range(max_pages):
            url = build_search_url(keyword, page_index)
            response = self.session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            page_tenders = parse_market_page(response.text, source_url=url)
            if not page_tenders:
                break
            tenders.extend(page_tenders)
        return tenders

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        for keyword in keywords:
            for tender in self.fetch_keyword(keyword):
                if tender.unique_key in seen:
                    continue
                seen.add(tender.unique_key)
                collected.append(tender)
        return collected
```

- [ ] **Step 4: Run source tests**

Run:

```powershell
python -m pytest tests/test_rts_source.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add tender_parser/sources tests/fixtures tests/test_rts_source.py
git commit -m "feat: parse rts market pages"
```

---

### Task 7: Full CLI Orchestration

**Files:**
- Modify: `tender_parser/cli.py`
- Create: `tests/test_cli.py` additional test content

- [ ] **Step 1: Extend CLI tests for end-to-end dry source**

Replace `tests/test_cli.py` with:

```python
from datetime import datetime
from pathlib import Path

from tender_parser.cli import run
from tender_parser.models import TenderRecord


class FakeSource:
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        return [
            TenderRecord(
                title="Поставка МФУ в Республику Крым",
                url="https://example.test/tender-1/",
                source="fake",
                tender_number="1",
                customer="Заказчик",
                region="Республика Крым",
                price=45_000.0,
                deadline=datetime(2026, 5, 25, 10, 0),
                raw_text="Поставка МФУ в Республику Крым",
            )
        ]


def test_run_dry_mode_creates_export_dirs(tmp_path: Path) -> None:
    result = run(["--dry-run", "--base-dir", str(tmp_path)])

    assert result == 0
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "exports").is_dir()


def test_run_with_fake_source_creates_database_and_exports(tmp_path: Path) -> None:
    result = run(
        ["--base-dir", str(tmp_path), "--now", "2026-05-19T12:00:00"],
        source=FakeSource(),
    )

    assert result == 0
    assert (tmp_path / "data" / "tenders.db").exists()
    assert (tmp_path / "exports" / "latest.json").exists()
    assert list((tmp_path / "exports").glob("tenders_*.xlsx"))
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_cli.py -v
```

Expected: FAIL because `run()` does not accept `source` and does not export.

- [ ] **Step 3: Implement orchestration**

Replace `tender_parser/cli.py` with:

```python
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from tender_parser.config import CATEGORY_KEYWORDS
from tender_parser.exporters.excel import export_excel
from tender_parser.exporters.json_exporter import export_json
from tender_parser.filters import evaluate_tender
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import RtsPublicSource
from tender_parser.storage import TenderStorage


class TenderSource(Protocol):
    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
    parser.add_argument("--base-dir", default=".", help="Project directory for data and exports")
    parser.add_argument("--dry-run", action="store_true", help="Create directories and exit")
    parser.add_argument("--now", default="", help="Override current datetime for tests, ISO format")
    return parser


def ensure_dirs(base_dir: Path) -> tuple[Path, Path]:
    data_dir = base_dir / "data"
    exports_dir = base_dir / "exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, exports_dir


def _all_keywords() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for terms in CATEGORY_KEYWORDS.values():
        for term in terms:
            normalized = term.strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                result.append(normalized)
    return result


def run(argv: Sequence[str] | None = None, source: TenderSource | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    data_dir, exports_dir = ensure_dirs(base_dir)
    if args.dry_run:
        return 0

    current_time = datetime.fromisoformat(args.now) if args.now else datetime.now()
    active_source = source or RtsPublicSource()
    raw_tenders = active_source.fetch_keywords(_all_keywords())
    evaluated = [evaluate_tender(tender, now=current_time) for tender in raw_tenders]

    storage = TenderStorage(data_dir / "tenders.db")
    storage.upsert_many(evaluated)

    matched = storage.fetch_by_status("matched")
    excluded = storage.fetch_by_status("excluded")

    date_stamp = current_time.strftime("%Y-%m-%d")
    excel_path = export_excel(matched, excluded, exports_dir / f"tenders_{date_stamp}.xlsx")
    json_path = export_json(matched, exports_dir / "latest.json")

    print(f"Найдено: {len(raw_tenders)}")
    print(f"Подходящие: {len(matched)}")
    print(f"Отсеянные: {len(excluded)}")
    print(f"Excel: {excel_path}")
    print(f"JSON: {json_path}")
    return 0


def main() -> int:
    return run()
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
python -m pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```powershell
python -m pytest -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add tender_parser/cli.py tests/test_cli.py
git commit -m "feat: orchestrate parser run"
```

---

### Task 8: Live Smoke Run And Documentation

**Files:**
- Create: `README.md`
- Modify: `tender_parser/sources/rts.py` only if live smoke exposes a parsing issue

- [ ] **Step 1: Create README**

Create `README.md`:

```markdown
# Парсер закупок RTS-Tender

Парсер ищет публичные актуальные закупки RTS-Tender по заданным категориям, фильтрует их по регионам, сумме от 30 000 рублей и стоп-темам, сохраняет историю в SQLite и выгружает Excel.

## Запуск

Двойной клик по файлу:

```text
Запустить_парсер.bat
```

После завершения откроется папка `exports`.

## Результаты

- `data/tenders.db` - локальная история закупок.
- `exports/tenders_YYYY-MM-DD.xlsx` - Excel с листами `Подходящие` и `Отсеянные`.
- `exports/latest.json` - JSON для будущей CRM.

## Ручной запуск

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m tender_parser run
```

## Ограничения первой версии

- Работает только с публичными страницами без авторизации.
- Не подает заявки автоматически.
- Не обходит капчу и закрытые разделы.
- Первый источник - публичный раздел `www.rosatom.rts-tender.ru/market/`.
```

- [ ] **Step 2: Run tests before live smoke**

Run:

```powershell
python -m pytest -v
```

Expected: PASS.

- [ ] **Step 3: Run live parser once**

Run:

```powershell
python -m tender_parser run
```

Expected: command exits with code 0, creates `data/tenders.db`, `exports/latest.json`, and `exports/tenders_YYYY-MM-DD.xlsx`. It is acceptable for `Подходящие` to be empty on the first run if the live source has no matching active tenders for the configured regions and keywords.

- [ ] **Step 4: Inspect generated files**

Run:

```powershell
Get-ChildItem data, exports
```

Expected: output includes `tenders.db`, `latest.json`, and at least one `tenders_*.xlsx` file.

- [ ] **Step 5: Commit docs and any live-smoke fixes**

Run:

```powershell
git add README.md tender_parser tests
git commit -m "docs: add parser usage guide"
```

---

## Final Verification

- [ ] Run all tests:

```powershell
python -m pytest -v
```

Expected: all tests pass.

- [ ] Run CLI smoke:

```powershell
python -m tender_parser run
```

Expected: exit code 0 and export files created.

- [ ] Check git status:

```powershell
git status --short
```

Expected: clean working tree after final commit.

## Self-Review

- Spec coverage: the plan covers one-click launch, public source, regions, keywords, stop terms including medicines, minimum price, active deadline, SQLite, Excel, JSON, tests, and README.
- Placeholder scan: no reserved placeholder tokens, incomplete sections, or vague test steps remain.
- Type consistency: `TenderRecord`, `evaluate_tender`, `TenderStorage`, `export_excel`, `export_json`, `RtsPublicSource`, and CLI signatures are introduced before later tasks use them.
