from tender_parser import config


def test_regional_search_matrix_covers_all_product_and_region_pairs() -> None:
    assert len(config.REGIONAL_SEARCH_QUERIES) == (
        len(config.SEARCH_QUERY_TERMS) * len(config.SEARCH_REGION_TERMS)
    )
    assert "мфу Симферополь" in config.REGIONAL_SEARCH_QUERIES
    assert "кондиционер Севастополь" in config.REGIONAL_SEARCH_QUERIES
    assert "сервер Республика Крым" in config.REGIONAL_SEARCH_QUERIES
    assert "электротехническая продукция Запорожская область" in config.REGIONAL_SEARCH_QUERIES
    assert "разъединитель Республика Крым" in config.REGIONAL_SEARCH_QUERIES
    assert "трансформатор тока Симферополь" in config.REGIONAL_SEARCH_QUERIES
    assert "металлическая мебель Херсонская область" in config.REGIONAL_SEARCH_QUERIES
    assert "бытовая химия Севастополь" in config.REGIONAL_SEARCH_QUERIES
    assert "кабельная продукция Запорожская область" in config.REGIONAL_SEARCH_QUERIES


def test_public_source_query_lists_share_the_regional_matrix() -> None:
    assert config.EIS_SEARCH_QUERIES == config.REGIONAL_SEARCH_QUERIES
    assert config.ETP_GPB_SEARCH_QUERIES == [
        *config.CUSTOMER_DISCOVERY_REGION_QUERIES,
        *config.REGIONAL_SEARCH_QUERIES,
    ]
    assert config.ROSTENDER_SEARCH_QUERIES == [
        *config.CUSTOMER_DISCOVERY_REGION_QUERIES,
        *config.REGIONAL_SEARCH_QUERIES,
    ]


def test_b2b_queries_include_customer_discovery_regions_and_product_terms() -> None:
    assert config.B2B_SEARCH_QUERIES == [
        *config.CUSTOMER_DISCOVERY_REGION_QUERIES,
        *config.SEARCH_QUERY_TERMS,
    ]
    assert config.B2B_SEARCH_QUERIES[:4] == [
        "Республика Крым",
        "Севастополь",
        "Запорожская область",
        "Херсонская область",
    ]
    assert "мфу" in config.B2B_SEARCH_QUERIES
    assert "картридж" in config.B2B_SEARCH_QUERIES
    assert "сетевое оборудование" in config.B2B_SEARCH_QUERIES
    assert "изолятор" in config.B2B_SEARCH_QUERIES
    assert "ячейка 10 кв" in config.B2B_SEARCH_QUERIES
    assert "металлическая мебель" in config.B2B_SEARCH_QUERIES


def test_dictionary_covers_high_value_business_aliases() -> None:
    categories = config.CATEGORY_KEYWORDS

    assert "источник бесперебойного питания" in categories["Резервное электропитание и ИБП"]
    assert "насосное оборудование" in config.SEARCH_QUERY_TERMS
    assert "программное обеспечение" in config.SEARCH_QUERY_TERMS
    assert "вентиляционное оборудование" in categories["Климатическая техника"]
    assert "холодильное оборудование" in categories["Бытовая техника"]
    assert "уборочный инвентарь" in categories["Хозяйственные товары и уборка"]
    assert "шкаф архивный" in categories["Офисная, архивная и складская мебель"]
    assert "кабельная продукция" in categories["Электротехника и оборудование"]
