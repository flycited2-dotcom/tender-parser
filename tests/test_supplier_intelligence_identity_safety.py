"""Supplier facts in quoted customer mail must not merge unrelated senders."""

from tender_parser.supplier_intelligence.extraction import split_quoted
from tender_parser.supplier_intelligence.resolver import Identity, match_candidates


def test_long_mail_separator_starts_quoted_history() -> None:
    result = split_quoted(
        "Предлагаем оборудование.\nС уважением, продавец\n"
        "----------------\nКому: sales@vendor.ru\n"
        "Наш ИНН: 9111008629, ООО «Покупатель»"
    )
    assert "Наш ИНН" not in result["new_message_body"]
    assert "Наш ИНН" in result["quoted_history"]


def test_shared_phone_and_inn_do_not_merge_distinct_sender_domains() -> None:
    candidate = match_candidates(
        Identity(inn="9111008629", domain="dealmed.ru", email="sales@dealmed.ru", phone="+79785792995"),
        [{"supplier_id": "SUP-000019", "inn": "9111008629", "domain": "rest-metal.ru",
          "primary_email": "sales@rest-metal.ru", "primary_phone": "+79785792995"}],
        [],
    )
    assert candidate == []
