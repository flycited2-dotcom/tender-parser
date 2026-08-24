from tender_parser.climate_routing import is_climate_request


def test_detects_climate_equipment_and_installation() -> None:
    assert is_climate_request("Поставка сплит-системы")
    assert is_climate_request("Монтаж кондиционера в административном здании")
    assert is_climate_request("Промышленная VRF система")
    assert is_climate_request("Приточная вентиляционная установка")


def test_does_not_route_unrelated_goods_to_climate_hub() -> None:
    assert not is_climate_request("Ноутбук Graviton")
    assert not is_climate_request("Клейкая лента канцелярская")
