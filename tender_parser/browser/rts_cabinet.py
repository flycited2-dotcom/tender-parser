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
