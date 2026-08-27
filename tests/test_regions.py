from tender_parser.regions import (
    detect_city,
    detect_delivery_region,
    detect_non_target_region,
    detect_region,
    region_bucket,
)


def test_detect_city_extracts_target_city_separately_from_region() -> None:
    assert detect_city("Республика Крым, г. Симферополь, ул. Ленина") == "Симферополь"
    assert detect_city("Запорожская область, г. Мелитополь") == "Мелитополь"
    assert detect_city("Херсонская область, Новая Каховка") == "Новая Каховка"
    assert detect_city("Республика Крым") is None
    assert detect_city("Москва, ул. Крымский Вал") is None


def test_detect_region_finds_crimean_cities() -> None:
    assert detect_region("Поставка МФУ, г. Ялта") == "Крым"
    assert detect_region("Керчь, ул. Ленина") == "Крым"
    assert detect_region("пгт Щёлкино") == "Крым"


def test_detect_region_understands_declensions_and_abbreviations() -> None:
    assert detect_region("в Республике Крым") == "Республика Крым"
    assert detect_region("Респ. Крым") == "Республика Крым"
    assert detect_region("Запорожская обл., г. Мелитополь") == "Запорожская область"
    assert detect_region("Херсонской области") == "Херсонская область"
    assert detect_region("город Геническ") == "Херсонская область"


def test_detect_region_covers_target_region_towns() -> None:
    assert detect_region("г. Судак, ул. Ленина") == "Крым"
    assert detect_region("Каменка-Днепровская, склад № 1") == "Запорожская область"
    assert detect_region("г. Новая Каховка") == "Херсонская область"
    assert detect_region("г. Алешки") == "Херсонская область"


def test_detect_region_prefers_specific_over_generic() -> None:
    assert detect_region("Республика Крым, г. Симферополь") == "Симферополь"
    assert detect_region("город Севастополь") == "Севастополь"


def test_detect_region_returns_none_for_non_target() -> None:
    assert detect_region("г. Москва") is None
    assert detect_region("Поставка полов и половиков") is None
    assert detect_region("") is None
    assert detect_region(None) is None


def test_detect_region_ignores_lookalike_places() -> None:
    assert detect_region("г. Москва, ул. Крымский Вал, д. 9") is None
    assert detect_region("г. Крымск Краснодарского края") is None
    assert detect_region("поставка в Крымске") is None
    assert detect_region("коньяк армянский пятилетний") is None
    assert detect_region("музей-заповедник Херсонес Таврический") is None
    assert detect_region("г. Белогорск, Амурская область") is None
    assert detect_region("Москва, ул. Крымская, д. 12") is None


def test_detect_region_keeps_real_crimean_adjectives() -> None:
    assert detect_region("КРЫМСКАЯ ТАМОЖНЯ") == "Крым"
    assert detect_region("ГУП РК Крымэнерго") == "Крым"
    assert detect_region("г. Армянск, ул. Мира") == "Крым"
    assert detect_region("г. Херсон, склад №2") == "Херсонская область"
    assert detect_region("г. Белогорск, Республика Крым") == "Республика Крым"


def test_region_bucket_groups_simferopol_with_crimea() -> None:
    assert region_bucket("г. Симферополь") == "crimea"
    assert region_bucket("Республика Крым") == "crimea"
    assert region_bucket("Севастополь") == "sevastopol"
    assert region_bucket("Геническ") == "kherson"
    assert region_bucket("Мелитополь") == "zaporizhzhia"
    assert region_bucket("Москва") == ""


def test_detect_delivery_region_requires_delivery_context() -> None:
    assert (
        detect_delivery_region("Заказчик Москва. Адрес места поставки: г. Севастополь")
        == "Севастополь"
    )
    assert detect_delivery_region("Фильтр: Республика Крым, Севастополь") is None


def test_detect_non_target_region_returns_explicit_other_region() -> None:
    assert detect_non_target_region("Доставка до г. Магадан") == "магадан"
    assert detect_non_target_region("Республика Хакасия") == "хакас"
    assert detect_non_target_region("г. Москва") == "москва"


def test_detect_non_target_region_yields_to_any_target_region() -> None:
    assert detect_non_target_region("Заказчик Москва, доставка в Республику Крым") is None
