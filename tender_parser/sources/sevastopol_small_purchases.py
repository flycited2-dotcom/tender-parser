from __future__ import annotations

from time import monotonic
from typing import Iterable

import requests

from tender_parser.config import HTTP_TIMEOUT_SECONDS
from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult, SourceHealth


SEVASTOPOL_SMALL_PURCHASES_SOURCE = "sevastopol-small-purchases"
# Current address from the regional 2026 small-purchases regulation.
SEVASTOPOL_SMALL_PURCHASES_SHOWCASE_URL = (
    "http://rks.sevzakaz.ru/zakupki-malogo-obema/oos-rks-001-001"
)
IMPORT_FALLBACK_DETAIL = (
    "резерв: выгрузить открытый реестр в CSV/XLSX, положить файл в папку imports/; "
    "штатный ImportFolderSource подхватит его в этом же запуске"
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Tender-Parser/0.5"


class SevastopolSmallPurchasesAdapter:
    """Honest placeholder for the official showcase until a stable API is verified.

    The official page is public by regulation, but its current machine-readable
    contract cannot be verified reliably. The adapter therefore never reports a
    successful collection. Optional probing is diagnostics-only and does not
    attempt authentication, CAPTCHA handling, or TLS verification bypasses.
    """

    source_name = SEVASTOPOL_SMALL_PURCHASES_SOURCE

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
        probe_live: bool = False,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,*/*",
            }
        )
        self.timeout_seconds = timeout_seconds
        self.probe_live = probe_live

    def fetch_keywords(self, keywords: Iterable[str]) -> list[TenderRecord]:
        return self.fetch_with_report(keywords).tenders

    def fetch_with_report(self, keywords: Iterable[str]) -> SourceFetchResult:
        del keywords
        started_at = monotonic()
        if not self.probe_live:
            detail = (
                "автосбор пропущен: у официальной витрины Севастополя пока не подтвержден "
                f"устойчивый публичный API; {IMPORT_FALLBACK_DETAIL}"
            )
            return SourceFetchResult(
                health=[
                    SourceHealth(
                        source=self.source_name,
                        status="skipped",
                        found=0,
                        elapsed_seconds=round(monotonic() - started_at, 3),
                        detail=detail,
                    )
                ]
            )

        try:
            response = self.session.get(
                SEVASTOPOL_SMALL_PURCHASES_SHOWCASE_URL,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            return self._error_result(started_at, "timeout", f"тайм-аут: {exc}")
        except requests.exceptions.SSLError as exc:
            return self._error_result(started_at, "ssl_error", f"ошибка TLS: {exc}")
        except requests.RequestException as exc:
            return self._error_result(started_at, "error", f"сетевая ошибка: {exc}")

        detail = (
            "официальная страница доступна, но устойчивый публичный контракт данных "
            f"не подтвержден; карточки не объявлены собранными; {IMPORT_FALLBACK_DETAIL}"
        )
        return SourceFetchResult(
            health=[
                SourceHealth(
                    source=self.source_name,
                    status="skipped",
                    found=0,
                    elapsed_seconds=round(monotonic() - started_at, 3),
                    detail=detail,
                )
            ]
        )

    def _error_result(
        self,
        started_at: float,
        status: str,
        reason: str,
    ) -> SourceFetchResult:
        detail = (
            f"официальная витрина Севастополя недоступна: {reason}; "
            f"{IMPORT_FALLBACK_DETAIL}"
        )
        return SourceFetchResult(
            health=[
                SourceHealth(
                    source=self.source_name,
                    status=status,  # type: ignore[arg-type]
                    found=0,
                    elapsed_seconds=round(monotonic() - started_at, 3),
                    detail=detail,
                )
            ],
            errors=[detail],
        )
