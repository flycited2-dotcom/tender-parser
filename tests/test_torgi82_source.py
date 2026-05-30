import json
from datetime import datetime
from pathlib import Path

from tender_parser.sources.torgi82 import Torgi82Source, build_search_url, parse_search_payload


SAMPLE_PAYLOAD = json.loads(Path("tests/fixtures/torgi82_search_sample.json").read_text(encoding="utf-8"))


def test_build_search_url_points_to_public_json_endpoint() -> None:
    assert build_search_url() == "https://etp.torgi82.ru/searchServlet"


def test_parse_search_payload_extracts_tenders() -> None:
    tenders = parse_search_payload(SAMPLE_PAYLOAD)

    assert len(tenders) == 2
    assert tenders[0].source == "torgi82"
    assert tenders[0].tender_number == "32616066162"
    assert tenders[0].title == "Поставка МФУ и принтеров для школы"
    assert tenders[0].url.startswith("https://etp.torgi82.ru/app/LotCard/page")
    assert tenders[0].customer == 'МБОУ "Крымская школа"'
    assert tenders[0].price == 524_529.0
    assert tenders[0].deadline == datetime(2026, 6, 10, 8, 0)
    assert tenders[0].published_at == datetime(2026, 5, 29, 0, 0)
    assert tenders[0].status == "Идет прием заявок"
    assert "26.20.16.120" in tenders[0].raw_text


class SearchResponse:
    def json(self) -> dict[str, object]:
        return SAMPLE_PAYLOAD

    def raise_for_status(self) -> None:
        return None


class SearchSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int) -> SearchResponse:
        self.requested_urls.append(url)
        return SearchResponse()


def test_fetch_keywords_requests_search_endpoint_once() -> None:
    session = SearchSession()
    source = Torgi82Source(session=session)

    tenders = source.fetch_keywords(["ignored"])

    assert len(tenders) == 2
    assert session.requested_urls == ["https://etp.torgi82.ru/searchServlet"]
