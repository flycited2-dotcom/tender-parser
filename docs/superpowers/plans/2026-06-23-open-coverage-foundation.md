# Open Tender Coverage Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox ('- [ ]') syntax for tracking.

**Goal:** Expand daily public tender collection with B2B-Center, an 80-query regional matrix, and explicit confidence for manual review without excluding useful cards that lack a deadline.

**Architecture:** Retain the existing 'TenderSource.fetch_keywords' contract. Add a public B2B-Center HTML adapter, derive source query lists from one product/region matrix, and extend 'TenderRecord' with a persisted confidence label.

**Tech Stack:** Python 3.14, requests, BeautifulSoup, SQLite, openpyxl, pytest.

## Global Constraints

- Do not bypass CAPTCHA, authentication, rate limits, or closed sections.
- Do not store tokens, passwords, certificates, or cabinet exports in Git.
- Keep 'TenderSource.fetch_keywords(keywords)' backward compatible.
- Use TDD: run each focused test red before its production change, then green.
- Preserve the last successful Excel and JSON files when every source is unavailable.
- Keep 'data/', 'exports/', and 'logs/' local and untracked.

## Scope Boundary

This is the first independent delivery. Alternative ЕИС/XML services and ЭТП ГПБ API
require an approved endpoint; Tender.Pro pagination and Торги82 GWT pagination
require a verified public request contract; cabinet-only sources require
permitted credentials. Each belongs to a later plan, rather than a guessed adapter.

## File Structure

- 'tender_parser/models.py': confidence type and tender card field.
- 'tender_parser/filters.py': exact, probable, manual-review and excluded decisions.
- 'tender_parser/storage.py': SQLite migration and confidence round-trip.
- 'tender_parser/exporters/excel.py' and 'json_exporter.py': CRM export field.
- 'tender_parser/config.py': shared 16 x 5 query matrix.
- 'tender_parser/sources/b2b_center.py': B2B-Center public table adapter.
- 'tender_parser/cli.py' and 'tender_parser/dedup.py': source activation and rank.
- 'tests/fixtures/b2b_center_market_sample.html' and 'tests/test_b2b_center_source.py':
  reproducible B2B parsing contract.
- 'README.md', 'docs/MEMORY.md', 'docs/HANDOFF.md': operator documentation.

---

### Task 1: Persist Review Confidence

**Files:**
- Modify: 'tender_parser/models.py'
- Modify: 'tender_parser/filters.py'
- Modify: 'tender_parser/storage.py'
- Modify: 'tender_parser/exporters/excel.py'
- Modify: 'tender_parser/exporters/json_exporter.py'
- Test: 'tests/test_filters.py'
- Test: 'tests/test_storage.py'
- Test: 'tests/test_exporters.py'

**Interfaces:**
- Produces 'MatchConfidence = Literal["точное", "вероятное", "ручная проверка"]'.
- Adds 'TenderRecord.match_confidence: MatchConfidence | None = None'.
- Keeps 'filter_status' values unchanged: 'matched', 'review', 'excluded'.
- A verified card is 'точное'; one missing price or deadline is 'вероятное'; absent
  region or several missing fields is 'ручная проверка'.

- [x] **Step 1: Write failing filter and JSON tests**

Add to 'tests/test_filters.py':

~~~python
def test_evaluate_tender_marks_verified_card_exact() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.match_confidence == "точное"


def test_evaluate_tender_reviews_unknown_deadline_as_probable() -> None:
    result = evaluate_tender(make_tender(deadline=None), now=NOW)

    assert result.filter_status == "review"
    assert result.match_confidence == "вероятное"
    assert result.exclude_reason == "требуется проверка: срок подачи не указан"


def test_evaluate_tender_marks_missing_region_as_manual_review() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ", region=None, raw_text="Поставка МФУ"),
        now=NOW,
    )

    assert result.filter_status == "review"
    assert result.match_confidence == "ручная проверка"
~~~

In 'tests/test_exporters.py', set 'match_confidence="точное"' in
'make_tender()' and add:

~~~python
assert data["items"][0]["match_confidence"] == "точное"
~~~

- [x] **Step 2: Run the focused tests red**

~~~powershell
python -m pytest tests/test_filters.py tests/test_exporters.py -q
~~~

Expected: failures because the field does not exist and an unknown deadline is
currently excluded.

- [x] **Step 3: Implement the model and filter decision**

In 'tender_parser/models.py', define and use the field:

~~~python
MatchConfidence = Literal["точное", "вероятное", "ручная проверка"]


@dataclass(frozen=True)
class TenderRecord:
    # existing fields stay unchanged
    filter_status: FilterStatus = "excluded"
    match_confidence: MatchConfidence | None = None
    matched_terms: list[str] = field(default_factory=list)
~~~

In 'tender_parser/filters.py', import 'MatchConfidence'. Make '_exclude()' set
'match_confidence=None'. Replace '_review()' with:

~~~python
def _review(
    tender: TenderRecord,
    *,
    category: str,
    terms: list[str],
    reason: str,
    region: str | None,
    confidence: MatchConfidence,
) -> TenderRecord:
    return replace(
        tender,
        filter_status="review",
        match_confidence=confidence,
        category=category,
        include_reason=_include_reason(
            category,
            terms,
            region,
            tender.price,
            deadline_is_active=tender.deadline is not None,
        ),
        exclude_reason=f"требуется проверка: {reason}",
        matched_terms=terms,
    )
~~~

Change '_include_reason()' to accept 'deadline_is_active: bool' and append:

~~~python
parts.append("срок подачи: активен" if deadline_is_active else "срок подачи: не указан")
~~~

Delete the current early branch that excludes a card when 'tender.deadline is
None'. Replace the remaining deadline, region and price branches in
'evaluate_tender()' after category detection with:

~~~python
if tender.deadline is not None and tender.deadline <= current:
    return _exclude(tender, "срок подачи истек")

region = _first_matching_term(searchable, REGION_TERMS)
if tender.region and not region:
    return _exclude(tender, "регион не целевой")

if tender.price is not None and tender.price < MIN_PRICE_RUB:
    return _exclude(tender, f"сумма меньше {MIN_PRICE_RUB}")

missing: list[str] = []
if tender.deadline is None:
    missing.append("срок подачи не указан")
if not region:
    missing.append("регион не найден")
if tender.price is None:
    missing.append("сумма не указана")
if missing:
    confidence: MatchConfidence = (
        "вероятное"
        if missing in (["срок подачи не указан"], ["сумма не указана"])
        else "ручная проверка"
    )
    return _review(
        tender,
        category=category,
        terms=terms,
        reason="; ".join(missing),
        region=region,
        confidence=confidence,
    )

return replace(
    tender,
    filter_status="matched",
    match_confidence="точное",
    category=category,
    include_reason=_include_reason(
        category, terms, region, tender.price, deadline_is_active=True
    ),
    exclude_reason="",
    matched_terms=terms,
)
~~~

In 'tender_parser/exporters/json_exporter.py', add this field after
'filter_status':

~~~python
"match_confidence": tender.match_confidence,
~~~

In 'tender_parser/exporters/excel.py', add '"уверенность"' after '"категория"'
in 'HEADERS', and add 'tender.match_confidence or ""' after the category value
in '_append_rows()'.

In 'tests/test_exporters.py', update the title-cell assertions from 'C2' to
'D2', because the inserted confidence column becomes column C.

- [x] **Step 4: Add the SQLite migration and storage test**

Add to 'tests/test_storage.py':

~~~python
def test_storage_round_trips_match_confidence(tmp_path: Path) -> None:
    storage = TenderStorage(tmp_path / "tenders.db")
    tender = TenderRecord(
        title="Поставка МФУ",
        url="https://example.test/tender-1/",
        source="test",
        tender_number="1",
        price=45_000.0,
        deadline=datetime(2026, 5, 25, 10, 0),
        filter_status="matched",
        match_confidence="точное",
        discovered_at=datetime(2026, 5, 19, 12, 0),
    )

    storage.upsert_many([tender])

    assert storage.fetch_by_status("matched")[0].match_confidence == "точное"
~~~

In 'TenderStorage._init_schema()', add 'match_confidence TEXT' after
'filter_status TEXT NOT NULL' in the create-table SQL. Then run:

~~~python
columns = {
    str(row["name"])
    for row in conn.execute("PRAGMA table_info(tenders)").fetchall()
}
if "match_confidence" not in columns:
    conn.execute("ALTER TABLE tenders ADD COLUMN match_confidence TEXT")
~~~

Add 'match_confidence' to the INSERT list, placeholders and 'ON CONFLICT' set.
Pass 'tender.match_confidence' in the INSERT tuple and
'match_confidence=row["match_confidence"]' in '_row_to_record()'.

Add a legacy-schema test that creates the previous 'tenders' table without
'match_confidence', initializes 'TenderStorage', and asserts that
'PRAGMA table_info(tenders)' contains the new column.

- [x] **Step 5: Run focused tests green**

~~~powershell
python -m pytest tests/test_filters.py tests/test_storage.py tests/test_exporters.py -q
~~~

Expected: all selected tests pass.

- [x] **Step 6: Commit the confidence feature**

~~~powershell
git add tender_parser/models.py tender_parser/filters.py tender_parser/storage.py tender_parser/exporters/excel.py tender_parser/exporters/json_exporter.py tests/test_filters.py tests/test_storage.py tests/test_exporters.py
git commit -m "Add tender review confidence"
~~~

### Task 2: Derive the 80-Query Regional Matrix

**Files:**
- Modify: 'tender_parser/config.py'
- Modify: 'tests/test_cli.py'
- Create: 'tests/test_config.py'

**Interfaces:**
- Produces 'SEARCH_QUERY_TERMS', 'SEARCH_REGION_TERMS',
  'REGIONAL_SEARCH_QUERIES' and 'B2B_SEARCH_QUERIES'.
- The regional matrix has exactly 16 product terms x 5 regions = 80 entries.
- ЕИС, ЭТП ГПБ and Rostender use this same matrix; B2B-Center uses product-only
  queries because its public list may not expose delivery region.

- [x] **Step 1: Write failing matrix tests**

Create 'tests/test_config.py':

~~~python
from tender_parser.config import (
    B2B_SEARCH_QUERIES,
    EIS_SEARCH_QUERIES,
    ETP_GPB_SEARCH_QUERIES,
    REGIONAL_SEARCH_QUERIES,
    ROSTENDER_SEARCH_QUERIES,
)


def test_regional_search_matrix_covers_all_product_and_region_pairs() -> None:
    assert len(REGIONAL_SEARCH_QUERIES) == 80
    assert "мфу Симферополь" in REGIONAL_SEARCH_QUERIES
    assert "кондиционер Севастополь" in REGIONAL_SEARCH_QUERIES
    assert "сервер Крым" in REGIONAL_SEARCH_QUERIES
    assert "электротехническая продукция Запорожская область" in REGIONAL_SEARCH_QUERIES
    assert "металлическая мебель Херсонская область" in REGIONAL_SEARCH_QUERIES


def test_public_source_query_lists_share_the_regional_matrix() -> None:
    assert EIS_SEARCH_QUERIES == REGIONAL_SEARCH_QUERIES
    assert ETP_GPB_SEARCH_QUERIES == REGIONAL_SEARCH_QUERIES
    assert ROSTENDER_SEARCH_QUERIES == REGIONAL_SEARCH_QUERIES


def test_b2b_queries_do_not_require_region_in_listing_title() -> None:
    assert len(B2B_SEARCH_QUERIES) == 16
    assert "мфу" in B2B_SEARCH_QUERIES
    assert "сетевое оборудование" in B2B_SEARCH_QUERIES
    assert "металлическая мебель" in B2B_SEARCH_QUERIES
~~~

Add to 'tests/test_cli.py':

~~~python
def test_all_keywords_includes_expanded_network_and_electrical_terms() -> None:
    keywords = _all_keywords()

    assert "сетевое оборудование" in keywords
    assert "точка доступа" in keywords
    assert "электротехническая продукция" in keywords
~~~

- [x] **Step 2: Run matrix tests red**

~~~powershell
python -m pytest tests/test_config.py tests/test_cli.py -q
~~~

Expected: collection fails because the constants do not exist.

- [x] **Step 3: Implement the single source of truth**

In 'tender_parser/config.py', insert before source query lists:

~~~python
SEARCH_QUERY_TERMS = [
    "мфу",
    "принтер",
    "оргтехника",
    "расходные материалы",
    "компьютерная техника",
    "сервер",
    "сетевое оборудование",
    "ибп",
    "кондиционер",
    "климатическое оборудование",
    "бытовая техника",
    "канцелярские товары",
    "электротехническая продукция",
    "сейф",
    "стеллаж",
    "металлическая мебель",
]

SEARCH_REGION_TERMS = [
    "Симферополь",
    "Севастополь",
    "Крым",
    "Запорожская область",
    "Херсонская область",
]

REGIONAL_SEARCH_QUERIES = [
    f"{term} {region}"
    for term in SEARCH_QUERY_TERMS
    for region in SEARCH_REGION_TERMS
]

B2B_SEARCH_QUERIES = SEARCH_QUERY_TERMS

ROSTENDER_SEARCH_QUERIES = REGIONAL_SEARCH_QUERIES
ETP_GPB_SEARCH_QUERIES = REGIONAL_SEARCH_QUERIES
EIS_SEARCH_QUERIES = REGIONAL_SEARCH_QUERIES
~~~

Remove the old hand-written lists. Add these terms to existing category lists:

~~~python
# Компьютерная техника и периферия
"сетевое оборудование",
"точка доступа",

# Климатическая техника
"ремонт кондиционера",
"монтаж сплит-системы",

# Бытовая техника
"бытовая техника",

# Канцелярия и офис
"канцтовары",

# Электротехника и оборудование
"электротехническая продукция",
"электротовары",
"металлическая мебель",
~~~

- [x] **Step 4: Run matrix tests green**

~~~powershell
python -m pytest tests/test_config.py tests/test_cli.py tests/test_filters.py -q
~~~

Expected: all selected tests pass.

- [x] **Step 5: Commit the query matrix**

~~~powershell
git add tender_parser/config.py tests/test_config.py tests/test_cli.py
git commit -m "Expand regional tender search matrix"
~~~

### Task 3: Add the B2B-Center Public Source

**Files:**
- Create: 'tender_parser/sources/b2b_center.py'
- Create: 'tests/fixtures/b2b_center_market_sample.html'
- Create: 'tests/test_b2b_center_source.py'
- Modify: 'tender_parser/cli.py'
- Modify: 'tender_parser/dedup.py'
- Modify: 'tests/test_cli.py'

**Interfaces:**
- Produces 'B2BCenterSource.fetch_keywords(keywords) -> list[TenderRecord]'.
- Produces 'build_search_url(query: str) -> str' for public B2B-Center market
  with 'f_keyword' and 'searching=1'.
- Produces 'parse_market_page(html, source_url) -> list[TenderRecord]'.
- Cards without delivery region or price enter manual review.

- [x] **Step 1: Add fixture and failing parser tests**

Create 'tests/fixtures/b2b_center_market_sample.html':

~~~html
<table class="search-results">
  <tbody>
    <tr>
      <td>
        <small>Офисная техника</small>
        <a class="search-results-title" href="/market/mfu/tender-4499001/">
          Запрос предложений № 4499001
          <div class="search-results-title-desc">Поставка МФУ и принтеров для офиса</div>
        </a>
      </td>
      <td><a>ООО "Крымский заказчик"</a></td>
      <td class="nowrap">23.06.2026 10:15</td>
      <td class="nowrap">30.06.2026 12:00</td>
    </tr>
    <tr>
      <td>
        <small>Электротехника</small>
        <a class="search-results-title" href="/market/cable/tender-4499002/">
          Запрос цен № 4499002
          <div class="search-results-title-desc">Поставка кабеля</div>
        </a>
      </td>
      <td><a>АО "Электросеть"</a></td>
      <td class="nowrap">23.06.2026 11:00</td>
      <td class="nowrap">01.07.2026 14:30</td>
    </tr>
  </tbody>
</table>
~~~

Create 'tests/test_b2b_center_source.py':

~~~python
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

from tender_parser.sources.b2b_center import (
    B2BCenterSource,
    build_search_url,
    parse_market_page,
)


SAMPLE_HTML = Path("tests/fixtures/b2b_center_market_sample.html").read_text(encoding="utf-8")


def test_build_search_url_uses_b2b_public_keyword_contract() -> None:
    decoded = unquote_plus(build_search_url("мфу"))

    assert decoded.startswith("https://www.b2b-center.ru/market/?")
    assert "f_keyword=мфу" in decoded
    assert "searching=1" in decoded


def test_parse_market_page_extracts_public_tenders() -> None:
    tenders = parse_market_page(SAMPLE_HTML, "https://www.b2b-center.ru/market/")

    assert len(tenders) == 2
    assert tenders[0].source == "b2b-center"
    assert tenders[0].tender_number == "4499001"
    assert tenders[0].title == "Поставка МФУ и принтеров для офиса"
    assert tenders[0].customer == 'ООО "Крымский заказчик"'
    assert tenders[0].price is None
    assert tenders[0].region is None
    assert tenders[0].published_at == datetime(2026, 6, 23, 10, 15)
    assert tenders[0].deadline == datetime(2026, 6, 30, 12, 0)
    assert tenders[0].url == "https://www.b2b-center.ru/market/mfu/tender-4499001/"
    assert "Офисная техника" in tenders[0].raw_text


class MarketResponse:
    text = SAMPLE_HTML

    def raise_for_status(self) -> None:
        return None


class MarketSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> MarketResponse:
        self.requested_urls.append(url)
        return MarketResponse()


def test_fetch_keywords_uses_configured_queries_and_deduplicates() -> None:
    session = MarketSession()
    source = B2BCenterSource(session=session, queries=["мфу", "принтер"])

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert len(session.requested_urls) == 2
    assert "f_keyword=" in session.requested_urls[0]
~~~

- [x] **Step 2: Run B2B tests red**

~~~powershell
python -m pytest tests/test_b2b_center_source.py -q
~~~

Expected: collection fails because the source module does not exist.

- [x] **Step 3: Implement the B2B source**

Create 'tender_parser/sources/b2b_center.py':

~~~python
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from tender_parser.config import B2B_SEARCH_QUERIES, HTTP_TIMEOUT_SECONDS
from tender_parser.models import TenderRecord
from tender_parser.sources.rts import SourceFetchError


B2B_MARKET_URL = "https://www.b2b-center.ru/market/"
B2B_SOURCE_NAME = "b2b-center"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.3"


def build_search_url(query: str) -> str:
    params = {"f_keyword": query, "searching": "1"}
    return f"{B2B_MARKET_URL}?{urlencode(params)}"


def parse_market_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for row in soup.select("table.search-results tbody tr"):
        cells = row.find_all("td", recursive=False)
        title_link = row.select_one("a.search-results-title")
        if len(cells) < 4 or title_link is None or not title_link.get("href"):
            continue
        number_match = re.search(r"№\s*(\d+)", _text(title_link))
        title = _text(title_link.select_one(".search-results-title-desc")) or _text(title_link)
        if not number_match or not title:
            continue
        raw_text = " ".join(_text(cell) for cell in cells if _text(cell))
        tenders.append(
            TenderRecord(
                title=title,
                url=urljoin(source_url, str(title_link["href"])),
                source=B2B_SOURCE_NAME,
                tender_number=number_match.group(1),
                customer=_text(cells[1]) or None,
                deadline=_parse_datetime(_text(cells[3])),
                published_at=_parse_datetime(_text(cells[2])),
                status="Актуально",
                discovered_at=datetime.now(),
                raw_text=raw_text,
            )
        )
    return tenders


class B2BCenterSource:
    def __init__(
        self,
        session: requests.Session | None = None,
        queries: list[str] | None = None,
        max_errors: int = 3,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.queries = queries or B2B_SEARCH_QUERIES
        self.max_errors = max_errors

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        collected: list[TenderRecord] = []
        seen: set[str] = set()
        errors: list[str] = []
        for query in self.queries or list(keywords):
            url = build_search_url(query)
            try:
                response = self.session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
                response.raise_for_status()
            except requests.RequestException as exc:
                errors.append(f"{query}: {exc}")
                if len(errors) >= self.max_errors:
                    break
                continue
            for tender in parse_market_page(response.text, source_url=url):
                dedupe_key = tender.tender_number or tender.unique_key
                if dedupe_key not in seen:
                    seen.add(dedupe_key)
                    collected.append(tender)
        if not collected and errors:
            raise SourceFetchError(f"B2B-Center недоступен: {'; '.join(errors)}")
        return collected


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _text(element: object | None) -> str:
    if element is None:
        return ""
    return element.get_text(" ", strip=True)  # type: ignore[attr-defined]
~~~

- [x] **Step 4: Activate the source and rank**

In 'tender_parser/cli.py', import 'B2BCenterSource' and insert
'B2BCenterSource()' immediately after 'Torgi82Source()' in the first inner
composite. In 'tender_parser/dedup.py', add:

~~~python
"b2b-center": 65,
~~~

to 'SOURCE_PRIORITY'. In 'tests/test_cli.py', import 'B2BCenterSource', assert
it at index 3, and move EAT, ЕИС and Rostender assertions to indexes 4, 5 and 6.

- [x] **Step 5: Run B2B tests green**

~~~powershell
python -m pytest tests/test_b2b_center_source.py tests/test_cli.py tests/test_dedup.py -q
~~~

Expected: all selected tests pass.

- [x] **Step 6: Commit B2B-Center ingestion**

~~~powershell
git add tender_parser/sources/b2b_center.py tender_parser/cli.py tender_parser/dedup.py tests/fixtures/b2b_center_market_sample.html tests/test_b2b_center_source.py tests/test_cli.py
git commit -m "Add B2B Center public source"
~~~

### Task 4: Document, Verify, and Publish

**Files:**
- Modify: 'README.md'
- Modify: 'docs/MEMORY.md'
- Modify: 'docs/HANDOFF.md'

**Interfaces:**
- Documents 'точное', 'вероятное' and 'ручная проверка' for CRM users.
- Documents B2B-Center as a source whose public list can lack price and region.

- [x] **Step 1: Update operator documentation**

Add to 'README.md':

~~~markdown
- 'точное' -- товар, целевой регион, сумма от 30 000 рублей и активный срок подтверждены.
- 'вероятное' -- товар и регион подтверждены, но не указана сумма или срок подачи.
- 'ручная проверка' -- товар интересен, но карточка не подтвердила регион или содержит несколько неполных полей.
~~~

Add B2B-Center to the public-source list and state that cards without delivery
region or price intentionally appear on 'На проверку'. Add to both
'docs/MEMORY.md' and 'docs/HANDOFF.md':

~~~markdown
- 'B2BCenterSource' reads the permitted public market table by product query.
- B2B cards without a target-region field are retained as 'ручная проверка'.
- The 80-query matrix is shared by ЕИС, ЭТП ГПБ and Rostender; each source still stops according to its own error limit.
~~~

- [x] **Step 2: Run the complete automated suite**

~~~powershell
python -m pytest -q
~~~

Expected: all tests pass with no collection errors.

- [x] **Step 3: Run the permitted B2B live smoke test**

~~~powershell
@'
from tender_parser.sources.b2b_center import B2BCenterSource

tenders = B2BCenterSource(queries=["мфу"]).fetch_keywords([])
print(f"B2B-Center cards: {len(tenders)}")
for tender in tenders[:3]:
    print(tender.tender_number, tender.title, tender.deadline)
'@ | .\.venv\Scripts\python.exe -
~~~

Expected: prints public B2B-Center cards or raises explicit 'SourceFetchError';
it never logs in or bypasses a CAPTCHA.

- [x] **Step 4: Run the full parser and inspect deliverables**

~~~powershell
python -m tender_parser run
Get-Content -Raw -Encoding UTF8 exports\run_report.json
~~~

Verify:

~~~text
exports/tenders_YYYY-MM-DD.xlsx exists and starts with the "Новые" sheet.
exports/latest.json contains actionable cards from the current run.
exports/new_tenders.json contains only first-seen actionable cards.
exports/run_report.json contains B2BCenterSource with ok, empty, or error status.
~~~

If every source fails, verify return code 2 and that previous Excel/JSON files
remain unchanged.

- [ ] **Step 5: Commit documentation and push**

~~~powershell
git add README.md docs/MEMORY.md docs/HANDOFF.md
git commit -m "Document expanded tender coverage"
git push target codex/rts-tender-parser
~~~

## Plan Self-Review

- Spec coverage: Task 1 delivers confidence and manual review; Task 2 delivers
  category/region breadth; Task 3 delivers B2B-Center; Task 4 delivers health
  visibility, documentation, tests and live verification.
- Placeholder scan: no task asks an implementer to guess an API. Each source
  uses a concrete permitted contract.
- Type consistency: 'MatchConfidence' is defined before filters, storage and
  exporters use it. 'B2BCenterSource', 'build_search_url' and
  'parse_market_page' are defined before 'cli.py' imports the source.
