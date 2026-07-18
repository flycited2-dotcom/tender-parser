from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from time import sleep

from tender_parser.rts_accumulator import RtsAccumulator
from tender_parser.sources.rts_cabinet import detect_cabinet_state, parse_cabinet_page
from tender_parser.sources.rts_poisk import parse_poisk_page


RECONNECT_DELAY_SECONDS = 5.0
# Динамические страницы поиска меняются каждый poll: без лимитов снапшоты
# съедают гигабайты за ночь. Один снапшот на URL, немного на сессию.
MAX_SNAPSHOTS_PER_SESSION = 5

# Бейдж-статус в углу страницы RTS: только чтение DOM плюс один div, сайт не трогаем.
BADGE_JS = """
(text) => {
  let el = document.getElementById('tender-parser-badge');
  if (!el) {
    el = document.createElement('div');
    el.id = 'tender-parser-badge';
    el.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483647;' +
      'background:#1a1a19;color:#ffffff;padding:10px 14px;border-radius:8px;' +
      'font:13px/1.4 Arial,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.35);pointer-events:none;';
    document.body.appendChild(el);
  }
  el.textContent = text;
}
"""


def collect_from_page(html: str, url: str, accumulator: RtsAccumulator) -> tuple[int, int] | None:
    """Добавляет видимую выдачу кабинета или поиска в накопитель; None, если результатов нет."""
    if "/poisk" in url:
        tenders = parse_poisk_page(html, url)
        if not tenders:
            return None
        return accumulator.add_many(tenders)
    if detect_cabinet_state(html, url) != "results":
        return None
    tenders = parse_cabinet_page(html, url)
    if not tenders:
        return None
    return accumulator.add_many(tenders)


def badge_text(added: int, total: int) -> str:
    if added:
        return f"Накопитель RTS: {total} строк (+{added} новых)"
    return f"Накопитель RTS: {total} строк (страница уже добавлена)"


def save_snapshot(html: str, url: str, diagnostics_dir: Path, seen_hashes: set[str]) -> Path | None:
    """Сохраняет вёрстку нераспознанной страницы один раз на содержимое — для настройки парсера."""
    digest = hashlib.sha1(html.encode("utf-8", "ignore")).hexdigest()[:12]
    if digest in seen_hashes:
        return None
    seen_hashes.add(digest)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    path = diagnostics_dir / f"rts_poisk_{datetime.now():%Y%m%d_%H%M%S}_{digest}.html"
    path.write_text(f"<!-- {url} -->\n{html}", encoding="utf-8")
    return path


class RtsCabinetWatcher:
    """Следит за открытой выдачей RTS в Chrome и сам добавляет каждую страницу в накопитель."""

    def __init__(
        self,
        db_path: Path,
        debug_url: str = "http://127.0.0.1:9222",
        poll_seconds: float = 2.0,
        diagnostics_dir: Path | None = None,
    ) -> None:
        self.db_path = db_path
        self.debug_url = debug_url
        self.poll_seconds = poll_seconds
        self.diagnostics_dir = diagnostics_dir or db_path.parent.parent / "diagnostics"
        self._snapshot_hashes: set[str] = set()
        self._snapshot_urls: set[str] = set()

    def run_forever(self) -> int:
        from playwright.sync_api import sync_playwright

        accumulator = RtsAccumulator(self.db_path)
        print("Автосбор RTS запущен. Листайте выдачу в Chrome — страницы добавляются сами.", flush=True)
        print("Остановить: Ctrl+C или закрыть это окно.", flush=True)
        try:
            with sync_playwright() as playwright:
                while True:
                    try:
                        browser = playwright.chromium.connect_over_cdp(self.debug_url)
                    except Exception as exc:
                        reason = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
                        print(
                            f"Chrome недоступен ({reason}); повтор через {RECONNECT_DELAY_SECONDS:.0f} сек. "
                            "Если Chrome закрыт — запустите Открыть_RTS_кабинет_Chrome.bat.",
                            flush=True,
                        )
                        sleep(RECONNECT_DELAY_SECONDS)
                        continue
                    self._watch_browser(browser, accumulator)
                    sleep(RECONNECT_DELAY_SECONDS)
        except KeyboardInterrupt:
            total = len(accumulator.load_all())
            print(f"Автосбор остановлен. Всего в накопителе RTS: {total}.", flush=True)
            print("Дальше: запустите Отчет_по_накопленному_RTS.bat.", flush=True)
            return 0

    def _watch_browser(self, browser, accumulator: RtsAccumulator) -> None:
        while True:
            try:
                # Обрыв CDP не бросает исключений в этот поток — connected надо
                # спрашивать явно, иначе цикл молча крутится по пустым вкладкам.
                if not browser.is_connected():
                    print("Chrome отключился — переподключаюсь...", flush=True)
                    return
                pages = [
                    page
                    for context in browser.contexts
                    for page in context.pages
                    if "rts-tender.ru" in page.url
                ]
            except Exception:
                print(f"Связь с Chrome потеряна; повтор через {RECONNECT_DELAY_SECONDS:.0f} сек.", flush=True)
                return
            for page in pages:
                self._poll_page(page, accumulator)
            sleep(self.poll_seconds)

    def _poll_page(self, page, accumulator: RtsAccumulator) -> None:
        try:
            url = page.url
            html = page.content()
            if page.url != url:
                return  # пользователь перешёл на другую страницу между чтениями
        except Exception:
            # Страница перегружается или закрыта — попробуем на следующем проходе.
            return
        try:
            result = collect_from_page(html, url, accumulator)
        except sqlite3.Error as exc:
            # Молчать нельзя: пользователь часами листает, думая что сбор идёт.
            print(
                f"Не удалось сохранить страницу в накопитель ({exc}). "
                "Возможно, идет отчет по этой же базе — повторю на следующем проходе.",
                flush=True,
            )
            return
        try:
            if result is not None:
                added, total = result
                if added:
                    print(f"+{added} новых строк, всего в накопителе: {total}", flush=True)
                page.evaluate(BADGE_JS, badge_text(added, total))
                return
            if "/poisk" in url and detect_cabinet_state(html, url) not in {"login", "blocked"}:
                if url not in self._snapshot_urls and len(self._snapshot_urls) < MAX_SNAPSHOTS_PER_SESSION:
                    saved = save_snapshot(html, url, self.diagnostics_dir, self._snapshot_hashes)
                    if saved:
                        self._snapshot_urls.add(url)
                        print(
                            f"Поиск RTS пока не распознается; вёрстка сохранена: diagnostics/{saved.name}",
                            flush=True,
                        )
                page.evaluate(BADGE_JS, "Поиск RTS: вёрстка сохранена для настройки парсера")
        except Exception:
            return
