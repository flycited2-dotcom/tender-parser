from tender_parser.supplier_search import SupplierProduct
from tender_parser.universal_routing import UniversalProductRouter


def _product(source: str, name: str) -> SupplierProduct:
    return SupplierProduct(
        sku=f"{source}-1",
        name=name,
        purchase_price_gross=100,
        stock_status="unknown",
        is_available=False,
        delivery_days=None,
        source=source,
        supplier_name=source,
    )


class Gateway:
    def __init__(self, products=(), error=None):
        self.products = list(products)
        self.error = error
        self.calls = []

    def search(self, query, *, limit=10):
        self.calls.append(query)
        if self.error:
            raise self.error
        return len(self.products), self.products[:limit]


def test_arbitrary_product_uses_live_private_price_capability_first():
    prices = Gateway([_product("private_price", "Маска медицинская трёхслойная")])
    itp = Gateway([])
    router = UniversalProductRouter(itp_gateway=itp, private_price_gateway=prices)
    result = router.search("маска медицинская")
    assert result["primary_source"] == "private_supplier_prices"
    assert result["classification"]["closed_category_list_used"] is False
    assert [stage["source"] for stage in result["route"][:2]] == ["private_supplier_prices", "itp"]


def test_unknown_product_falls_through_to_itp_then_open_market():
    prices = Gateway([])
    itp = Gateway([_product("itp", "Специальное изделие")])
    router = UniversalProductRouter(itp_gateway=itp, private_price_gateway=prices)
    result = router.search("специальное изделие")
    assert result["primary_source"] == "itp"
    assert result["route"][-1]["source"] == "manufacturer_and_open_market"
    assert result["route"][-1]["status"] == "required"


def test_source_error_does_not_block_remaining_route():
    prices = Gateway(error=OSError("offline"))
    itp = Gateway([_product("itp", "Шина автомобильная")])
    router = UniversalProductRouter(itp_gateway=itp, private_price_gateway=prices)
    result = router.search("шина автомобильная")
    assert result["route"][0]["status"] == "error"
    assert result["primary_source"] == "itp"


def test_climate_semantics_add_specialized_source_without_changing_universal_core():
    climate = Gateway([_product("climate", "Сплит-система 3.5 кВт")])
    router = UniversalProductRouter(
        itp_gateway=Gateway([]),
        private_price_gateway=Gateway([]),
        climate_gateway=climate,
    )
    result = router.search("поставка и монтаж сплит-системы 3.5 кВт")
    assert result["route"][0]["source"] == "climate_hub"
    assert result["primary_source"] == "climate_hub"
