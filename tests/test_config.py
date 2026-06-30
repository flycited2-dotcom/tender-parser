from tender_parser import config


def test_regional_search_matrix_covers_all_product_and_region_pairs() -> None:
    assert len(config.REGIONAL_SEARCH_QUERIES) == 110
    assert "мфу Симферополь" in config.REGIONAL_SEARCH_QUERIES
    assert "кондиционер Севастополь" in config.REGIONAL_SEARCH_QUERIES
    assert "сервер Крым" in config.REGIONAL_SEARCH_QUERIES
    assert "электротехническая продукция Запорожская область" in config.REGIONAL_SEARCH_QUERIES
    assert "разъединитель Крым" in config.REGIONAL_SEARCH_QUERIES
    assert "трансформатор тока Симферополь" in config.REGIONAL_SEARCH_QUERIES
    assert "металлическая мебель Херсонская область" in config.REGIONAL_SEARCH_QUERIES


def test_public_source_query_lists_share_the_regional_matrix() -> None:
    assert config.EIS_SEARCH_QUERIES == config.REGIONAL_SEARCH_QUERIES
    assert config.ETP_GPB_SEARCH_QUERIES == config.REGIONAL_SEARCH_QUERIES
    assert config.ROSTENDER_SEARCH_QUERIES == config.REGIONAL_SEARCH_QUERIES


def test_b2b_queries_do_not_require_region_in_listing_title() -> None:
    assert len(config.B2B_SEARCH_QUERIES) == 22
    assert "мфу" in config.B2B_SEARCH_QUERIES
    assert "сетевое оборудование" in config.B2B_SEARCH_QUERIES
    assert "изолятор" in config.B2B_SEARCH_QUERIES
    assert "ячейка 10 кВ" in config.B2B_SEARCH_QUERIES
    assert "металлическая мебель" in config.B2B_SEARCH_QUERIES
