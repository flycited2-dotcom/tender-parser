"""Universal, capability-driven source routing for arbitrary tender products."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from tender_parser.climate_routing import is_climate_request
from tender_parser.supplier_search import SupplierProduct, SupplierProductGateway, _evaluate_product
from tender_parser.tender_case import LineItem


@dataclass(frozen=True)
class RouteStage:
    source: str
    priority: int
    status: str
    total: int = 0
    returned: int = 0
    error: str = ""
    reason: str = ""


class UniversalProductRouter:
    """Probe owner-controlled catalogs, then I-T-P, without a closed category list.

    Product categories are not encoded here.  General private prices are searched
    by their live content.  Specialized sources can add a semantic predicate (the
    climate hub is the first one); unknown products still follow the same route.
    """

    def __init__(
        self,
        *,
        itp_gateway: SupplierProductGateway | None,
        private_price_gateway: SupplierProductGateway | None = None,
        climate_gateway: SupplierProductGateway | None = None,
    ) -> None:
        self.itp_gateway = itp_gateway
        self.private_price_gateway = private_price_gateway
        self.climate_gateway = climate_gateway

    def search(
        self,
        query: str,
        *,
        required_specs: str = "",
        limit: int = 10,
    ) -> dict[str, object]:
        normalized_query = " ".join(query.split()).strip()
        if not normalized_query:
            raise ValueError("Поисковый запрос не может быть пустым")
        safe_limit = max(1, min(int(limit), 20))
        climate = is_climate_request(normalized_query, required_specs)
        source_plan: list[tuple[str, SupplierProductGateway | None, str]] = []
        if climate:
            source_plan.append(
                ("climate_hub", self.climate_gateway, "Специализированный каталог соответствует назначению товара")
            )
        source_plan.extend(
            (
                (
                    "private_supplier_prices",
                    self.private_price_gateway,
                    "Универсальная проверка по фактическому содержимому частных прайсов",
                ),
                ("itp", self.itp_gateway, "Широкий закрытый каталог I-T-P как следующий источник"),
            )
        )

        item = LineItem(
            line_id="route",
            name=normalized_query,
            quantity=Decimal("1"),
            required_specs=required_specs.strip(),
        )
        stages: list[RouteStage] = []
        grouped_products: dict[str, list[dict[str, Any]]] = {}
        primary_source = ""
        for priority, (source_name, gateway, reason) in enumerate(source_plan, start=1):
            if gateway is None:
                stages.append(
                    RouteStage(source_name, priority, "unavailable", reason=reason, error="Источник не настроен")
                )
                continue
            try:
                total, products = gateway.search(normalized_query, limit=safe_limit)
            except Exception as exc:  # isolated source failure must not stop the route
                stages.append(
                    RouteStage(source_name, priority, "error", reason=reason, error=f"{exc.__class__.__name__}: {str(exc)[:300]}")
                )
                continue
            evaluated = [_evaluate_product(item, product) for product in products]
            grouped_products[source_name] = [asdict(product) for product in evaluated]
            viable = [product for product in evaluated if product.compliance_status != "not_compliant"]
            status = "matched" if viable else ("rejected" if evaluated else "empty")
            stages.append(
                RouteStage(source_name, priority, status, total=total, returned=len(evaluated), reason=reason)
            )
            if viable and not primary_source:
                primary_source = source_name

        viable_count = sum(
            1
            for products in grouped_products.values()
            for product in products
            if product.get("compliance_status") != "not_compliant"
        )
        open_market_required = not primary_source or viable_count < 5
        return {
            "query": normalized_query,
            "required_specs": required_specs.strip(),
            "classification": {
                "strategy": "dynamic_source_capability_probe",
                "specialized_route": "climate" if climate else "none",
                "closed_category_list_used": False,
            },
            "primary_source": primary_source or "open_market",
            "route": [asdict(stage) for stage in stages]
            + [
                asdict(
                    RouteStage(
                        "manufacturer_and_open_market",
                        len(stages) + 1,
                        "required" if open_market_required else "verify",
                        reason=(
                            "Нужно расширить выборку минимум до 5, целевым образом до 10 вариантов"
                            if open_market_required
                            else "Нужно подтвердить ключевые характеристики у производителя"
                        ),
                    )
                )
            ],
            "products": grouped_products,
            "manual_checkpoint": (
                "Подтвердить остаток, срок и характеристики у поставщика; "
                "внешние письма не отправлены"
            ),
        }
