import pytest
import requests

from tender_parser.http import get_with_retry


class FakeResponse:
    status_code = 200
    text = "ok"

    def raise_for_status(self) -> None:
        return None


class ErrorResponse:
    status_code = 500

    def raise_for_status(self) -> None:
        raise requests.HTTPError("500")


class FlakySession:
    def __init__(self, failures: int, response: object | None = None) -> None:
        self.failures = failures
        self.response = response or FakeResponse()
        self.calls = 0

    def get(self, url: str, timeout: int) -> object:
        self.calls += 1
        if self.calls <= self.failures:
            raise requests.Timeout("boom")
        return self.response


def test_get_with_retry_recovers_from_timeout() -> None:
    session = FlakySession(failures=1)
    sleeps: list[float] = []

    response = get_with_retry(session, "https://example.test", timeout=5, sleeper=sleeps.append)

    assert response.text == "ok"
    assert session.calls == 2
    assert sleeps == [1.0]


def test_get_with_retry_raises_after_budget() -> None:
    session = FlakySession(failures=5)

    with pytest.raises(requests.Timeout):
        get_with_retry(session, "https://example.test", timeout=5, sleeper=lambda _: None)

    assert session.calls == 2


def test_get_with_retry_does_not_retry_http_errors() -> None:
    session = FlakySession(failures=0, response=ErrorResponse())

    with pytest.raises(requests.HTTPError):
        get_with_retry(session, "https://example.test", timeout=5, sleeper=lambda _: None)

    assert session.calls == 1
