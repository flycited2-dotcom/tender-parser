from __future__ import annotations

from time import sleep
from typing import Any, Callable

import requests

try:
    import truststore
except ImportError:  # pragma: no cover - старое окружение до обновления requirements
    truststore = None
else:
    # ЕИС использует цепочку, доступную системному хранилищу Windows, но не
    # всегда входящую в certifi. Проверку TLS не отключаем: используем CryptoAPI.
    truststore.inject_into_ssl()


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
    max_response_bytes: int | None = None,
) -> Any:
    """GET с повтором только на сетевых сбоях (timeout/connection), не на HTTP-ошибках."""
    wait = sleeper if sleeper is not None else sleep
    attempt = 0
    while True:
        try:
            if max_response_bytes is None:
                response = session.get(url, timeout=timeout)
            else:
                response = session.get(url, timeout=timeout, stream=True)
            if max_response_bytes is not None:
                try:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if len(body) + len(chunk) > max_response_bytes:
                            raise requests.RequestException('response size limit exceeded')
                        body.extend(chunk)
                    response._content = bytes(body)
                    response._content_consumed = True
                finally:
                    response.close()
            else:
                response.raise_for_status()
            return response
        except NON_RETRYABLE_EXCEPTIONS:
            raise
        except RETRYABLE_EXCEPTIONS:
            if attempt >= retries:
                raise
            wait(backoff_seconds * (attempt + 1))
            attempt += 1
