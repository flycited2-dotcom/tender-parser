from __future__ import annotations

from time import sleep
from typing import Any, Callable

import requests


RETRYABLE_EXCEPTIONS = (requests.Timeout, requests.ConnectionError)
# SSL-ошибки детерминированы: повтор лишь удваивает ожидание таймаута.
NON_RETRYABLE_EXCEPTIONS = (requests.exceptions.SSLError,)


def get_with_retry(
    session: Any,
    url: str,
    *,
    timeout: int,
    retries: int = 1,
    backoff_seconds: float = 1.0,
    sleeper: Callable[[float], None] | None = None,
) -> Any:
    """GET с повтором только на сетевых сбоях (timeout/connection), не на HTTP-ошибках."""
    wait = sleeper if sleeper is not None else sleep
    attempt = 0
    while True:
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except NON_RETRYABLE_EXCEPTIONS:
            raise
        except RETRYABLE_EXCEPTIONS:
            if attempt >= retries:
                raise
            wait(backoff_seconds * (attempt + 1))
            attempt += 1
