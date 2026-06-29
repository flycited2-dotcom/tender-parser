from __future__ import annotations

from collections.abc import Callable
from typing import Any


class RtsCabinetBrowserClient:
    def __init__(
        self,
        debug_url: str = "http://127.0.0.1:9222",
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.debug_url = debug_url
        self.playwright_factory = playwright_factory

    def read_current_page(self) -> tuple[str, str]:
        factory = self.playwright_factory
        if factory is None:
            from playwright.sync_api import sync_playwright

            factory = sync_playwright

        with factory() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.debug_url)
            for context in browser.contexts:
                for page in context.pages:
                    if "rts-tender.ru" in page.url:
                        page.wait_for_load_state("domcontentloaded", timeout=5000)
                        return page.url, page.content()
            raise RuntimeError("no RTS-Tender tab found in Chrome profile")
