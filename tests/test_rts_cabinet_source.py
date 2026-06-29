from __future__ import annotations

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
    assert tender.price == 45_000.0
    assert tender.deadline is not None
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
