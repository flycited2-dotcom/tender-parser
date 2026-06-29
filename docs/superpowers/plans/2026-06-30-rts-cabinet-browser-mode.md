# RTS Cabinet Browser Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first working RTS-Tender cabinet collection mode that connects to a manually logged-in Chrome profile, reads the visible RTS cabinet results, and sends the records through the existing tender report pipeline.

**Architecture:** Add a new source `RtsCabinetBrowserSource` that is separate from the public RTS source. The source delegates HTML/table parsing to a testable pure parser and delegates real browser access to a small Playwright/CDP adapter. CLI gets a new `--profile rts-cabinet`, plus two Windows launchers: one opens the isolated Chrome profile, the other runs the cabinet collection profile.

**Tech Stack:** Python 3, pytest, BeautifulSoup, requests, Playwright Python for local Chrome CDP connection, existing `TenderRecord`, `CompositeSource`, `SourceFetchResult`, Excel/JSON/HTML exporters.

## Global Constraints

- Do not store RTS passwords, PIN codes, certificates, EDS data, cookies, or tokens in Git.
- Do not bypass CAPTCHA, anti-bot pages, paid APIs, rate limits, or closed sections without user access.
- User logs into RTS manually in Chrome; automation only reads pages visible in that authenticated browser session.
- Chrome debug endpoint must be local only: `127.0.0.1`.
- Profile directory is `browser_profiles/rts_chrome` and must be ignored by Git.
- First increment is command-driven collection; no background keepalive clicks or hidden activity.
- If Chrome is closed, session is missing, or RTS shows login/CAPTCHA, the source must report health and avoid overwriting reports with misleading empty success.

---

## File Structure

- Create `tests/fixtures/rts_cabinet_results_sample.html`: representative cabinet result table fixture.
- Create `tests/fixtures/rts_cabinet_login_sample.html`: login/session-expired fixture.
- Create `tests/fixtures/rts_cabinet_blocked_sample.html`: CAPTCHA/blocked fixture.
- Create `tests/test_rts_cabinet_source.py`: parser, state detection, source-health tests.
- Modify `tests/test_cli.py`: add `rts-cabinet` profile test.
- Modify `tests/test_launchers.py`: add launcher and profile assertions.
- Create `tender_parser/sources/rts_cabinet.py`: pure parsing, source class, browser client protocol.
- Create `tender_parser/browser/__init__.py`: package marker.
- Create `tender_parser/browser/session.py`: Chrome debug endpoint checks.
- Create `tender_parser/browser/rts_cabinet.py`: Playwright/CDP browser client.
- Modify `tender_parser/cli.py`: add `rts-cabinet` profile and preserve report on cabinet access errors.
- Modify `requirements.txt`: add Playwright.
- Modify `.gitignore`: ignore `browser_profiles/`.
- Create `Открыть_RTS_кабинет_Chrome.bat`: opens isolated Chrome profile on local debug port.
- Create `Собрать_RTS_кабинет.bat`: runs `python -m tender_parser run --profile rts-cabinet`.
- Create `docs/RTS_CABINET_BROWSER_MODE.md`: user-facing workflow.
- Modify `README.md`, `docs/MEMORY.md`, `docs/HANDOFF.md`: short operational notes.

---

### Task 1: Pure RTS Cabinet HTML Parsing

**Files:**
- Create: `tests/fixtures/rts_cabinet_results_sample.html`
- Create: `tests/fixtures/rts_cabinet_login_sample.html`
- Create: `tests/fixtures/rts_cabinet_blocked_sample.html`
- Create: `tests/test_rts_cabinet_source.py`
- Create: `tender_parser/sources/rts_cabinet.py`

**Interfaces:**
- Produces: `parse_cabinet_page(html: str, source_url: str) -> list[TenderRecord]`
- Produces: `detect_cabinet_state(html: str, url: str) -> Literal["results", "login", "blocked", "unknown"]`
- Later tasks import these from `tender_parser.sources.rts_cabinet`.

- [ ] **Step 1: Add fixtures**

Add `tests/fixtures/rts_cabinet_results_sample.html` with a simple RTS-like table:

```html
<html>
  <body>
    <table class="registry">
      <thead>
        <tr>
          <th>Номер процедуры</th>
          <th>Наименование</th>
          <th>Заказчик</th>
          <th>Регион</th>
          <th>НМЦК</th>
          <th>Размещено</th>
          <th>Окончание подачи</th>
          <th>Статус</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>RTS-4455001</td>
          <td><a href="/trade/view/?id=4455001">Поставка МФУ в Республику Крым</a></td>
          <td>ГБУ РК Тест</td>
          <td>Республика Крым</td>
          <td>45 000,00 ₽</td>
          <td>29.06.2026</td>
          <td>05.07.2026 10:00</td>
          <td>Прием заявок</td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
```

Add `tests/fixtures/rts_cabinet_login_sample.html`:

```html
<html><body><form><input type="password" name="password"></form><h1>Вход в личный кабинет</h1></body></html>
```

Add `tests/fixtures/rts_cabinet_blocked_sample.html`:

```html
<html><body><h1>Проверка безопасности</h1><p>captcha</p></body></html>
```

- [ ] **Step 2: Write failing parser tests**

Add to `tests/test_rts_cabinet_source.py`:

```python
from pathlib import Path

from tender_parser.sources.rts_cabinet import detect_cabinet_state, parse_cabinet_page


FIXTURES = Path("tests/fixtures")


def test_parse_cabinet_page_extracts_visible_results() -> None:
    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")

    tenders = parse_cabinet_page(html, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx")

    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.source == "rts-cabinet"
    assert tender.tender_number == "RTS-4455001"
    assert tender.title == "Поставка МФУ в Республику Крым"
    assert tender.url == "https://223.rts-tender.ru/trade/view/?id=4455001"
    assert tender.customer == "ГБУ РК Тест"
    assert tender.region == "Республика Крым"
    assert tender.price == 45000.0
    assert tender.deadline.year == 2026
    assert tender.status == "Прием заявок"
    assert tender.detail_status == "enriched"
    assert tender.source_confidence == 0.9


def test_detect_cabinet_state_identifies_results_login_and_blocked() -> None:
    results = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    login = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    blocked = (FIXTURES / "rts_cabinet_blocked_sample.html").read_text(encoding="utf-8")

    assert detect_cabinet_state(results, "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx") == "results"
    assert detect_cabinet_state(login, "https://223.rts-tender.ru/login") == "login"
    assert detect_cabinet_state(blocked, "https://223.rts-tender.ru/captcha") == "blocked"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_rts_cabinet_source.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'tender_parser.sources.rts_cabinet'`.

- [ ] **Step 4: Implement minimal parser**

Create `tender_parser/sources/rts_cabinet.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tender_parser.models import TenderRecord
from tender_parser.text import normalize_text, parse_deadline, parse_price_rub


CabinetState = Literal["results", "login", "blocked", "unknown"]


def detect_cabinet_state(html: str, url: str) -> CabinetState:
    normalized = normalize_text(f"{url} {html}")
    if "captcha" in normalized or "проверка безопасности" in normalized:
        return "blocked"
    if "вход в личный кабинет" in normalized or 'type="password"' in html.lower() or "/login" in url.lower():
        return "login"
    if "номер процедуры" in normalized and ("наименование" in normalized or "нмцк" in normalized):
        return "results"
    return "unknown"


def parse_cabinet_page(html: str, source_url: str) -> list[TenderRecord]:
    soup = BeautifulSoup(html, "html.parser")
    tenders: list[TenderRecord] = []
    for table in soup.find_all("table"):
        headers = [_clean(cell.get_text(" ", strip=True)).lower() for cell in table.find_all("th")]
        if not _looks_like_results_table(headers):
            continue
        for row in table.select("tbody tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
            link = row.find("a", href=True)
            title = _title_from_row(values, link.get_text(" ", strip=True) if link else "")
            if not title:
                continue
            tenders.append(
                TenderRecord(
                    title=title,
                    url=urljoin(source_url, str(link["href"])) if link else source_url,
                    source="rts-cabinet",
                    tender_number=_value(headers, values, "номер процедуры") or values[0],
                    customer=_value(headers, values, "заказчик"),
                    region=_value(headers, values, "регион"),
                    price=parse_price_rub(_value(headers, values, "нмцк") or _value(headers, values, "цена") or ""),
                    deadline=parse_deadline(_value(headers, values, "окончание подачи") or ""),
                    published_at=parse_deadline(_value(headers, values, "размещено") or ""),
                    status=_value(headers, values, "статус"),
                    discovered_at=datetime.now(),
                    raw_text=" ".join(value for value in values if value),
                    detail_status="enriched",
                    source_confidence=0.9,
                )
            )
    return tenders


def _looks_like_results_table(headers: list[str]) -> bool:
    joined = " ".join(headers)
    return "номер" in joined and ("наименование" in joined or "нмцк" in joined or "заказчик" in joined)


def _value(headers: list[str], values: list[str], needle: str) -> str | None:
    for index, header in enumerate(headers):
        if needle in header and index < len(values):
            return values[index] or None
    return None


def _title_from_row(values: list[str], link_text: str) -> str:
    if link_text:
        return _clean(link_text)
    for value in values:
        if len(value) > 12 and not value.upper().startswith("RTS-"):
            return value
    return ""


def _clean(value: str) -> str:
    return " ".join(value.split())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_rts_cabinet_source.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add tests/fixtures/rts_cabinet_results_sample.html tests/fixtures/rts_cabinet_login_sample.html tests/fixtures/rts_cabinet_blocked_sample.html tests/test_rts_cabinet_source.py tender_parser/sources/rts_cabinet.py
git commit -m "Add RTS cabinet page parser"
```

---

### Task 2: Browser Client and Source Health

**Files:**
- Modify: `tests/test_rts_cabinet_source.py`
- Create: `tender_parser/browser/__init__.py`
- Create: `tender_parser/browser/session.py`
- Create: `tender_parser/browser/rts_cabinet.py`
- Modify: `tender_parser/sources/rts_cabinet.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `parse_cabinet_page`, `detect_cabinet_state`
- Produces: `class RtsCabinetBrowserSource`
- Produces: `class RtsCabinetBrowserClient`
- Produces: `check_chrome_debug_endpoint(debug_url: str) -> bool`

- [ ] **Step 1: Write failing source-health tests**

Append to `tests/test_rts_cabinet_source.py`:

```python
from tender_parser.sources.rts_cabinet import RtsCabinetBrowserSource, SourceFetchError


class FakeCabinetClient:
    def __init__(self, html: str, url: str = "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx") -> None:
        self.html = html
        self.url = url

    def read_current_page(self) -> tuple[str, str]:
        return self.url, self.html


def test_cabinet_source_returns_health_for_visible_results() -> None:
    html = (FIXTURES / "rts_cabinet_results_sample.html").read_text(encoding="utf-8")
    source = RtsCabinetBrowserSource(client=FakeCabinetClient(html))

    result = source.fetch_with_report(["мфу"])

    assert len(result.tenders) == 1
    assert result.health[0].source == "rts-cabinet"
    assert result.health[0].status == "ok"
    assert result.health[0].found == 1


def test_cabinet_source_raises_when_login_page_is_visible() -> None:
    html = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    source = RtsCabinetBrowserSource(client=FakeCabinetClient(html, "https://223.rts-tender.ru/login"))

    result = source.fetch_with_report(["мфу"])

    assert result.tenders == []
    assert result.health[0].status == "blocked"
    assert "login required" in result.health[0].detail
    assert result.errors


def test_cabinet_fetch_keywords_raises_when_not_authenticated() -> None:
    html = (FIXTURES / "rts_cabinet_login_sample.html").read_text(encoding="utf-8")
    source = RtsCabinetBrowserSource(client=FakeCabinetClient(html, "https://223.rts-tender.ru/login"))

    try:
        source.fetch_keywords(["мфу"])
    except SourceFetchError as exc:
        assert "login required" in str(exc)
    else:
        raise AssertionError("expected SourceFetchError")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rts_cabinet_source.py -q`

Expected: FAIL because `RtsCabinetBrowserSource` and `SourceFetchError` are not defined.

- [ ] **Step 3: Implement source wrapper**

Extend `tender_parser/sources/rts_cabinet.py`:

```python
from time import monotonic
from typing import Protocol

from tender_parser.run_report import SourceFetchResult, SourceHealth


class SourceFetchError(RuntimeError):
    pass


class CabinetBrowserClient(Protocol):
    def read_current_page(self) -> tuple[str, str]:
        ...


class RtsCabinetBrowserSource:
    def __init__(self, client: CabinetBrowserClient | None = None) -> None:
        self.client = client

    def fetch_keywords(self, keywords: list[str]) -> list[TenderRecord]:
        result = self.fetch_with_report(keywords)
        if result.errors:
            raise SourceFetchError("; ".join(result.errors))
        return result.tenders

    def fetch_with_report(self, keywords: list[str]) -> SourceFetchResult:
        started_at = monotonic()
        if self.client is None:
            from tender_parser.browser.rts_cabinet import RtsCabinetBrowserClient

            self.client = RtsCabinetBrowserClient()
        try:
            url, html = self.client.read_current_page()
        except Exception as exc:
            detail = f"chrome unavailable: {exc}"
            return SourceFetchResult(
                tenders=[],
                health=[SourceHealth("rts-cabinet", "skipped", 0, round(monotonic() - started_at, 3), detail)],
                errors=[detail],
            )
        state = detect_cabinet_state(html, url)
        if state == "login":
            detail = "login required: open RTS cabinet Chrome profile and sign in manually"
            return SourceFetchResult(
                tenders=[],
                health=[SourceHealth("rts-cabinet", "blocked", 0, round(monotonic() - started_at, 3), detail)],
                errors=[detail],
            )
        if state == "blocked":
            detail = "blocked: RTS shows captcha or anti-bot page"
            return SourceFetchResult(
                tenders=[],
                health=[SourceHealth("rts-cabinet", "blocked", 0, round(monotonic() - started_at, 3), detail)],
                errors=[detail],
            )
        if state == "unknown":
            detail = "unknown RTS cabinet page: open search results before running collector"
            return SourceFetchResult(
                tenders=[],
                health=[SourceHealth("rts-cabinet", "error", 0, round(monotonic() - started_at, 3), detail)],
                errors=[detail],
            )
        tenders = parse_cabinet_page(html, url)
        status = "ok" if tenders else "empty"
        return SourceFetchResult(
            tenders=tenders,
            health=[SourceHealth("rts-cabinet", status, len(tenders), round(monotonic() - started_at, 3))],
        )
```

- [ ] **Step 4: Add browser package and Playwright client**

Create `tender_parser/browser/__init__.py`:

```python
"""Browser automation helpers for manually authenticated cabinet sources."""
```

Create `tender_parser/browser/session.py`:

```python
from __future__ import annotations

import requests


def check_chrome_debug_endpoint(debug_url: str = "http://127.0.0.1:9222") -> bool:
    try:
        response = requests.get(f"{debug_url.rstrip('/')}/json/version", timeout=2)
    except requests.RequestException:
        return False
    return response.ok
```

Create `tender_parser/browser/rts_cabinet.py`:

```python
from __future__ import annotations


class RtsCabinetBrowserClient:
    def __init__(self, debug_url: str = "http://127.0.0.1:9222") -> None:
        self.debug_url = debug_url

    def read_current_page(self) -> tuple[str, str]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.debug_url)
            try:
                for context in browser.contexts:
                    for page in context.pages:
                        if "rts-tender.ru" in page.url:
                            page.wait_for_load_state("domcontentloaded", timeout=5000)
                            return page.url, page.content()
                raise RuntimeError("no RTS-Tender tab found in Chrome profile")
            finally:
                browser.close()
```

Add to `requirements.txt`:

```text
playwright==1.61.0
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_rts_cabinet_source.py -q`

Expected: PASS.

- [ ] **Step 6: Install dependency and run full tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: full test suite passes.

- [ ] **Step 7: Commit**

```powershell
git add requirements.txt tests/test_rts_cabinet_source.py tender_parser/sources/rts_cabinet.py tender_parser/browser/__init__.py tender_parser/browser/session.py tender_parser/browser/rts_cabinet.py
git commit -m "Add RTS cabinet browser source"
```

---

### Task 3: CLI Profile, Launchers, and Git Ignore

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_launchers.py`
- Modify: `tender_parser/cli.py`
- Modify: `.gitignore`
- Create: `Открыть_RTS_кабинет_Chrome.bat`
- Create: `Собрать_RTS_кабинет.bat`

**Interfaces:**
- Consumes: `RtsCabinetBrowserSource`
- Produces: CLI profile `--profile rts-cabinet`
- Produces: launchers for opening Chrome and running cabinet collection.

- [ ] **Step 1: Write failing CLI and launcher tests**

Add to `tests/test_cli.py` imports:

```python
from tender_parser.sources.rts_cabinet import RtsCabinetBrowserSource
```

Add test:

```python
def test_build_rts_cabinet_profile_runs_only_cabinet_source() -> None:
    source = build_source_for_profile("rts-cabinet")

    assert isinstance(source, CompositeSource)
    assert len(source.sources) == 1
    assert isinstance(source.sources[0], RtsCabinetBrowserSource)
```

Add to `tests/test_launchers.py`:

```python
def test_rts_cabinet_launchers_use_isolated_chrome_profile() -> None:
    open_text = (ROOT / "Открыть_RTS_кабинет_Chrome.bat").read_text(encoding="utf-8")
    collect_text = (ROOT / "Собрать_RTS_кабинет.bat").read_text(encoding="utf-8")

    assert "--remote-debugging-address=127.0.0.1" in open_text
    assert "--remote-debugging-port=9222" in open_text
    assert "browser_profiles\\rts_chrome" in open_text
    assert "python -m tender_parser run --profile rts-cabinet" in collect_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py::test_build_rts_cabinet_profile_runs_only_cabinet_source tests/test_launchers.py::test_rts_cabinet_launchers_use_isolated_chrome_profile -q`

Expected: FAIL because profile and launchers do not exist.

- [ ] **Step 3: Update CLI**

Modify `tender_parser/cli.py`:

```python
from tender_parser.sources.rts_cabinet import RtsCabinetBrowserSource
```

Change:

```python
RunProfile = Literal["full", "fast", "local", "rts", "rts-cabinet"]
```

Change parser choices:

```python
choices=["full", "fast", "local", "rts", "rts-cabinet"]
```

Add in `build_source_for_profile` before `fast`:

```python
if profile == "rts-cabinet":
    return CompositeSource([RtsCabinetBrowserSource()])
```

Change `preserve_error_report` call:

```python
preserve_error_report=args.profile in {"rts", "rts-cabinet"}
```

- [ ] **Step 4: Ignore browser profile**

Append to `.gitignore`:

```text
browser_profiles/
```

- [ ] **Step 5: Add launchers**

Create `Открыть_RTS_кабинет_Chrome.bat`:

```bat
@echo off
setlocal
cd /d "%~dp0"

if not exist "browser_profiles" mkdir "browser_profiles"

set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
    echo Google Chrome not found. Install Chrome or update CHROME_EXE in this file.
    pause
    exit /b 1
)

start "" "%CHROME_EXE%" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%CD%\browser_profiles\rts_chrome" "https://223.rts-tender.ru/"
```

Create `Собрать_RTS_кабинет.bat`:

```bat
@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install -r requirements.txt
)

call ".venv\Scripts\activate.bat"
python -m tender_parser run --profile rts-cabinet
pause
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_cli.py::test_build_rts_cabinet_profile_runs_only_cabinet_source tests/test_launchers.py::test_rts_cabinet_launchers_use_isolated_chrome_profile -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add .gitignore tender_parser/cli.py tests/test_cli.py tests/test_launchers.py "Открыть_RTS_кабинет_Chrome.bat" "Собрать_RTS_кабинет.bat"
git commit -m "Add RTS cabinet CLI profile and launchers"
```

---

### Task 4: Documentation and Operational Memory

**Files:**
- Create: `docs/RTS_CABINET_BROWSER_MODE.md`
- Modify: `README.md`
- Modify: `docs/MEMORY.md`
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: launchers and `--profile rts-cabinet`
- Produces: user instructions for manual login, collection, and troubleshooting.

- [ ] **Step 1: Write docs**

Create `docs/RTS_CABINET_BROWSER_MODE.md`:

```markdown
# RTS Cabinet Browser Mode

## Что это

Режим собирает закупки из личного кабинета RTS-Tender через отдельный профиль Google Chrome. Парсер не знает ваш пароль, PIN, сертификат или ЭЦП. Вы входите вручную, а скрипт читает видимую страницу выдачи.

## Первый запуск

1. Запустите `Открыть_RTS_кабинет_Chrome.bat`.
2. В открывшемся Chrome войдите в RTS-Tender вручную.
3. Откройте страницу поиска или реестра закупок.
4. Поставьте фильтры RTS: период, статус, регион, ключевые слова.
5. Запустите `Собрать_RTS_кабинет.bat`.

## Где отчет

После сбора смотрите:

- `exports/latest.html` - удобный ручной отчет;
- `exports/latest.json` - очередь для CRM;
- `exports/run_report.json` - состояние источника `rts-cabinet`.

## Если просит войти снова

Откройте `Открыть_RTS_кабинет_Chrome.bat`, войдите заново и повторите сбор. Парсер не обходит вход, CAPTCHA или блокировки.

## Безопасность

Профиль Chrome хранится локально в `browser_profiles/rts_chrome` и не коммитится в Git. Не запускайте Chrome debug profile на общей машине или с открытым удаленным доступом.
```

Add short README section:

```markdown
## RTS кабинет через Chrome

Для кабинетного RTS-сбора используйте `Открыть_RTS_кабинет_Chrome.bat`, вручную войдите в RTS и откройте выдачу закупок. Затем запустите `Собрать_RTS_кабинет.bat`. Подробно: `docs/RTS_CABINET_BROWSER_MODE.md`.
```

Add to `docs/MEMORY.md` and `docs/HANDOFF.md`:

```markdown
- RTS cabinet browser mode uses isolated Chrome profile `browser_profiles/rts_chrome`, local debug endpoint `127.0.0.1:9222`, CLI profile `--profile rts-cabinet`, and launchers `Открыть_RTS_кабинет_Chrome.bat` / `Собрать_RTS_кабинет.bat`.
```

- [ ] **Step 2: Run markdown/text search**

Run: `rg "rts-cabinet|RTS кабинет|browser_profiles" README.md docs`

Expected: matches in README, `docs/RTS_CABINET_BROWSER_MODE.md`, MEMORY, HANDOFF, spec, and plan.

- [ ] **Step 3: Commit**

```powershell
git add README.md docs/RTS_CABINET_BROWSER_MODE.md docs/MEMORY.md docs/HANDOFF.md
git commit -m "Document RTS cabinet browser workflow"
```

---

### Task 5: Verification and Live-Ready Smoke Checks

**Files:**
- No new files unless a prior task reveals a test failure.

**Interfaces:**
- Consumes all previous tasks.
- Produces verified branch ready for user login/live test.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rts_cabinet_source.py tests/test_cli.py tests/test_launchers.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run full tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: full test suite passes.

- [ ] **Step 3: Verify CLI profile does not overwrite reports when Chrome is absent**

Run in a temporary base directory:

```powershell
$tmp = Join-Path $env:TEMP ("rts-cabinet-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null
.\.venv\Scripts\python.exe -m tender_parser run --profile rts-cabinet --base-dir $tmp
```

Expected: exit code `0`, `exports/run_report.json` exists, and source `rts-cabinet` has status `skipped` or `blocked` with a clear detail. Existing reports must not be overwritten by a false successful empty source when Chrome is absent.

- [ ] **Step 4: Verify git status**

Run: `git status --short --branch`

Expected: clean working tree, branch ahead by implementation commits.

- [ ] **Step 5: Push**

Run: `git push target codex/rts-tender-parser`

Expected: push succeeds to `flycited2-dotcom/tender-parser`.

---

## Self-Review

Spec coverage:

- Chrome profile and manual login: Task 3 launchers, Task 4 docs.
- No credentials or CAPTCHA bypass: Global Constraints, Task 4 docs.
- Browser source and parser: Task 1 and Task 2.
- CLI profile: Task 3.
- Run report health: Task 2 and Task 5.
- Existing Excel/JSON/HTML pipeline: Task 3 routes source through existing CLI pipeline.
- Tests: Tasks 1, 2, 3, and 5.

Placeholder scan:

- No `TODO`, `TBD`, or unspecified "handle later" steps are used as implementation instructions.

Type consistency:

- `parse_cabinet_page(html: str, source_url: str) -> list[TenderRecord]` is introduced in Task 1 and consumed in Task 2.
- `detect_cabinet_state(html: str, url: str)` is introduced in Task 1 and consumed in Task 2.
- `RtsCabinetBrowserSource` is introduced in Task 2 and consumed in Task 3.
- CLI profile name is consistently `rts-cabinet`.
