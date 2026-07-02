# Parsing Quality Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Устранить тихие потери и ложные исключения целевых тендеров в конвейере парсинга (спека: `docs/superpowers/specs/2026-07-03-parsing-quality-improvements-design.md`, фаза 1).

**Architecture:** Точечные исправления существующих модулей + один новый модуль `tender_parser/regions.py` (единый региональный словарь). Все изменения проверяются офлайн-тестами pytest в стиле проекта (фикстуры, без сети).

**Tech Stack:** Python 3.11+, pytest, sqlite3, BeautifulSoup (без новых зависимостей).

**Verify после каждой задачи:** `python -m pytest -q` из корня, все тесты зелёные. Коммит после каждой задачи.

---

### Task 1: Нормализация текста и цен (`text.py`)

**Files:**
- Modify: `tender_parser/text.py`
- Test: `tests/test_text.py`

- [x] **Step 1: Failing-тесты** — в `tests/test_text.py`:

```python
def test_normalize_text_folds_yo_and_narrow_spaces() -> None:
    assert normalize_text("Щёлкино 100 000") == "щелкино 100 000"


def test_parse_price_rub_without_currency_marker_when_not_required() -> None:
    assert parse_price_rub("1 052 860,00", require_currency=False) == 1052860.0
    assert parse_price_rub("1 052 860,00") is None


def test_word_term_matches_uses_exception_table() -> None:
    assert word_term_matches("поставка мышей и клавиатур", "мышь") is True
    assert word_term_matches("соединения мышьяка", "мышь") is False
    assert word_term_matches("узи щитовидной железы", "щит") is False
    assert word_term_matches("щиты распределительные", "щит") is True
    assert word_term_matches("мониторинг цен", "монитор") is False
```

- [x] **Step 2: Реализация** — `normalize_text` чистит ` `/` ` и сводит `ё→е`; `parse_price_rub(value, *, require_currency=True)`; новая функция:

```python
TERM_SUFFIX_EXCEPTIONS = {
    "монитор": "инг",   # мониторинг
    "мышь": "як",       # мышьяк
    "щит": "овидн",     # щитовидная железа
}


def word_term_matches(text: str, term: str) -> bool:
    if not term:
        return False
    blocked = TERM_SUFFIX_EXCEPTIONS.get(term)
    suffix_guard = f"(?!{blocked})" if blocked else ""
    return re.search(rf"(?<![\w]){re.escape(term)}{suffix_guard}[\w-]*(?![\w])", text) is not None
```

- [x] **Step 3: pytest зелёный, commit** `Fold yo and narrow spaces, share word matching`

### Task 2: Общий word-matching в фильтрах и документах

**Files:**
- Modify: `tender_parser/filters.py:36-44` (`_category_term_matches` → `word_term_matches`, спец-ветку «монитор» удалить)
- Modify: `tender_parser/documents.py:90-97` (`_term_matches` single-word ветка → `word_term_matches`)
- Test: `tests/test_filters.py`

- [x] **Step 1: Failing-тесты** — в `tests/test_filters.py`:

```python
def test_evaluate_tender_does_not_treat_arsenic_as_mouse() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка соединений мышьяка в Республику Крым",
                    raw_text="Поставка соединений мышьяка в Республику Крым"),
        now=NOW,
    )
    assert result.filter_status == "excluded"


def test_evaluate_tender_does_not_treat_thyroid_as_switchboard() -> None:
    result = evaluate_tender(
        make_tender(title="УЗИ щитовидной железы в Республике Крым",
                    raw_text="УЗИ щитовидной железы"),
        now=NOW,
    )
    assert result.filter_status == "excluded"
```

- [x] **Step 2: Реализация, pytest зелёный (все старые монитор/трубопровод тесты не тронуты), commit** `Use shared word matching in filters and documents`

### Task 3: Единый региональный модуль `regions.py`

**Files:**
- Create: `tender_parser/regions.py`
- Test: `tests/test_regions.py`

- [x] **Step 1: Failing-тесты** — `tests/test_regions.py`:

```python
from tender_parser.regions import detect_region, region_bucket


def test_detect_region_finds_crimean_cities() -> None:
    assert detect_region("Поставка МФУ, г. Ялта") == "Крым"
    assert detect_region("Керчь, ул. Ленина") == "Крым"
    assert detect_region("щёлкино") == "Крым"


def test_detect_region_understands_declensions_and_abbreviations() -> None:
    assert detect_region("в Республике Крым") == "Республика Крым"
    assert detect_region("Респ. Крым") == "Республика Крым"
    assert detect_region("Запорожская обл., г. Мелитополь") == "Запорожская область"
    assert detect_region("Херсонской области") == "Херсонская область"


def test_detect_region_prefers_specific_over_generic() -> None:
    assert detect_region("Республика Крым, г. Симферополь") == "Симферополь"
    assert detect_region("город Севастополь") == "Севастополь"


def test_detect_region_returns_none_for_non_target() -> None:
    assert detect_region("г. Москва") is None
    assert detect_region("") is None


def test_region_bucket_groups_simferopol_with_crimea() -> None:
    assert region_bucket("г. Симферополь") == "crimea"
    assert region_bucket("Республика Крым") == "crimea"
    assert region_bucket("Севастополь") == "sevastopol"
    assert region_bucket("Геническ") == "kherson"
    assert region_bucket("Москва") == ""
```

- [x] **Step 2: Реализация** — словарь canonical → варианты (стемы, падежи, города; матчинг по префиксной границе слова `(?<![\w])`), порядок: Симферополь, Севастополь, Республика Крым, Крым (+города), Запорожская область (+Мелитополь, Бердянск, Энергодар, Токмак), Херсонская область (+Херсон-стем, Геническ, Скадовск, Каховка). `region_bucket` маппит канонику в bucket, Симферополь → `crimea`.

- [x] **Step 3: pytest зелёный, commit** `Add unified region dictionary`

### Task 4: Фильтры — регионы через `detect_region`, стоп-термы без заказчика

**Files:**
- Modify: `tender_parser/filters.py:105-125`
- Test: `tests/test_filters.py`

- [x] **Step 1: Failing-тесты**:

```python
def test_evaluate_tender_keeps_crimean_city_region() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ", region="г. Ялта", raw_text="Поставка МФУ"),
        now=NOW,
    )
    assert result.filter_status == "matched"
    assert "регион" in result.include_reason.lower()


def test_evaluate_tender_ignores_stop_terms_in_customer_name() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка МФУ в Республику Крым",
            customer="ГБУЗ Клинико-диагностическая лаборатория",
            raw_text="Поставка МФУ в Республику Крым ГБУЗ Клинико-диагностическая лаборатория",
        ),
        now=NOW,
    )
    assert result.filter_status == "matched"
```

- [x] **Step 2: Реализация** — регион: `region = detect_region(" ".join([title, region, customer, raw_text]))`, include_reason получает каноническое имя; стоп-термы: проверять по `title + raw_text` с вырезанной подстрокой `customer`:

```python
def _stop_searchable(tender: TenderRecord) -> str:
    raw_text = tender.raw_text
    if tender.customer:
        raw_text = raw_text.replace(tender.customer, " ")
    return normalize_text(" ".join([tender.title, raw_text]))
```

- [x] **Step 3: pytest зелёный (существующие регион/стоп-тесты обязаны пройти без правок), commit** `Detect regions via shared dictionary, scope stop terms to subject`

### Task 5: ЕИС и дедуп переходят на `regions.py`

**Files:**
- Modify: `tender_parser/sources/eis.py:20-27,161-169` (`REGION_VARIANTS` и `_extract_region` → `detect_region`)
- Modify: `tender_parser/dedup.py:21-27,74-79` (`REGION_BUCKETS`/`_region_bucket` → `regions.region_bucket`)
- Test: существующие `tests/test_eis_source.py`, `tests/test_dedup.py`

- [x] **Step 1: Реализация, pytest зелёный (фикстура ЕИС даёт «Республика Крым» и «Симферополь»), commit** `Reuse region dictionary in EIS and dedup`

### Task 6: Дедуп в CompositeSource по `unique_key`

**Files:**
- Modify: `tender_parser/sources/composite.py:76`
- Test: `tests/test_composite_source.py`

- [x] **Step 1: Failing-тест**:

```python
def test_composite_source_keeps_same_number_from_different_sources() -> None:
    class OtherSource:
        def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
            return [TenderRecord(title="Другой тендер", url="https://other.test/1",
                                 source="other", tender_number="1")]

    tenders = CompositeSource([GoodSource(), OtherSource()]).fetch_keywords(["мфу"])
    assert len(tenders) == 2
```

- [x] **Step 2: Реализация** — `dedupe_key = tender.unique_key`; pytest зелёный, commit `Deduplicate composite results by source-scoped key`

### Task 7: RTS — региональные endpoint'ы первыми

**Files:**
- Modify: `tender_parser/sources/rts.py:179` (стабильная сортировка: `region_hint` первыми)
- Test: `tests/test_rts_source.py`

- [x] **Step 1: Failing-тест** — дубль номера между rosatom и симферопольским endpoint'ом сохраняет карточку с `region_hint`:

```python
def test_fetch_with_report_visits_region_hinted_endpoints_first() -> None:
    session = SharedTenderSession()  # оба endpoint'а отдают один и тот же номер
    source = RtsPublicSource(session=session, endpoints=[
        RtsMarketEndpoint("https://www.rosatom.rts-tender.ru/market/", "rts-rosatom"),
        RtsMarketEndpoint("https://zakupki-simferopol.rts-tender.ru/market/", "rts-zakupki-simferopol", "Симферополь"),
    ])
    result = source.fetch_with_report(["мфу"])
    kept = [t for t in result.tenders if t.tender_number == "4455001"]
    assert kept and kept[0].region == "Симферополь"
```

- [x] **Step 2: Реализация** — `for endpoint in sorted(self.endpoints, key=lambda e: e.region_hint is None):`; pytest зелёный, commit `Visit region-hinted RTS endpoints first`

### Task 8: Хранилище — merge непустых полей + «впервые actionable»

**Files:**
- Modify: `tender_parser/storage.py:79-150`
- Test: `tests/test_storage.py`

- [x] **Step 1: Failing-тесты**:

```python
def test_storage_keeps_filled_fields_when_update_is_empty(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    full = ...  # region="Республика Крым", price=45000, customer="Заказчик"
    degraded = replace(full, region=None, price=None, customer=None)
    storage.upsert_many([full])
    storage.upsert_many([degraded])
    row = storage.fetch_by_status(degraded.filter_status)[0]
    assert row.region == "Республика Крым"
    assert row.price == 45_000.0
    assert row.customer == "Заказчик"


def test_storage_reports_promotion_to_actionable(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    excluded = ...  # review_priority="excluded"
    storage.upsert_many([excluded])
    promoted = replace(excluded, review_priority="hot", filter_status="matched")
    newly = storage.upsert_many([promoted])
    assert newly == [promoted]
    again = storage.upsert_many([promoted])
    assert again == []
```

- [x] **Step 2: Реализация** — в `ON CONFLICT DO UPDATE`: `customer=COALESCE(NULLIF(excluded.customer,''), customer)` и аналогично region/price/deadline/published_at/raw_text; перед вставкой читать `review_priority`, промо = был не в `{hot,review,wide}` → стал в них; возвращать first-seen + промо.

- [x] **Step 3: pytest зелёный, commit** `Merge non-empty fields on upsert and report promotions`

### Task 9: Импорты — устойчивость к битой строке

**Files:**
- Modify: `tender_parser/sources/imports.py:52-127` (`_read_file(path, errors)`, per-row `try/except ValueError`)
- Test: `tests/test_import_source.py`

- [x] **Step 1: Failing-тест**:

```python
def test_import_folder_source_skips_bad_row_and_keeps_good_ones(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "mix.csv").write_text(
        "Название;Ссылка\nПоставка МФУ;https://example.test/1\n;https://example.test/2\n",
        encoding="utf-8",
    )
    result = ImportFolderSource(imports_dir).fetch_with_report([])
    assert len(result.tenders) == 1
    assert result.health[0].status == "partial"
    assert "mix.csv" in result.health[0].detail
```

- [x] **Step 2: Реализация, pytest зелёный, commit** `Keep good import rows when one row is invalid`

### Task 10: Tender.Pro ключ из env + единый парсер цены в rts_cabinet/imports

**Files:**
- Modify: `tender_parser/sources/tender_pro.py:24-32,114-121` (`_api_key()` c `os.getenv("TENDER_PRO_API_KEY")`, дефолт — текущая константа)
- Modify: `.env.example` (строка `TENDER_PRO_API_KEY=`)
- Modify: `tender_parser/sources/rts_cabinet.py:200-216` и `tender_parser/sources/imports.py:216-227` → `parse_price_rub(value, require_currency=False)`
- Modify: `tender_parser/sources/eis.py:36` (`recordsPerPage=_50`, live-проверка в следующем запуске)
- Test: существующие + `tests/test_tender_pro_source.py`

- [x] **Step 1: Реализация, pytest зелёный, commit** `Read Tender.Pro key from env, unify price parsing, widen EIS page`

### Task 11: Финал

- [x] Полный `python -m pytest -q` зелёный.
- [x] Обновить `docs/HANDOFF.md` (раздел «Обновление 2026-07-03») и `README.md` при необходимости.
- [x] Commit `Document parsing quality phase 1`.

## Self-Review

- Spec coverage фазы 1: нормализация (T1), словарь категорий (T1-T2), регионы (T3-T5), стоп-термы (T4), дедуп (T6-T7), хранилище (T8), импорты (T9), Tender.Pro/ЕИС/цены (T10) — всё покрыто; фаза 2 сознательно вне плана.
- Типы согласованы: `word_term_matches(text, term) -> bool`, `detect_region(text) -> str | None`, `region_bucket(text) -> str`, `upsert_many -> list[TenderRecord]` (семантика расширена: first-seen + промо).
- Известное ограничение зафиксировано в спеке: переоценка слитых карточек — фаза 2.
