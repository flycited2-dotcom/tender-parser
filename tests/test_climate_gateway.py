import json
from types import SimpleNamespace

import requests

from tender_parser.supplier_search import (
    ClimateProductApiGateway,
    ClimateProductSshGateway,
    PrivatePriceSshGateway,
    climate_gateway_from_environment,
)


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "ok": True,
            "total": 1,
            "products": [
                {
                    "source": "breeze",
                    "supplierName": "Бриз",
                    "sku": "RC-12",
                    "name": "Royal Clima RC-12",
                    "purchasePriceGross": 31000,
                    "stockStatus": "available",
                    "isAvailable": True,
                    "stockQuantity": 7,
                    "vendor": "Royal Clima",
                    "attributes": [{"label": "Источник", "key": "source", "value": "breeze"}],
                }
            ],
        }


def test_climate_gateway_uses_separate_url_and_token(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("tender_parser.supplier_search.requests.post", fake_post)
    gateway = ClimateProductApiGateway("https://climate.example/search", "x" * 32)
    total, products = gateway.search("Royal Clima", limit=50)

    assert total == 1
    assert products[0].sku == "RC-12"
    assert products[0].source == "breeze"
    assert products[0].supplier_name == "Бриз"
    assert captured["url"] == "https://climate.example/search"
    assert captured["headers"]["Authorization"] == f"Bearer {'x' * 32}"
    assert captured["json"]["limit"] == 50
    assert json.dumps(captured["json"], ensure_ascii=False)


def test_http_catalog_retries_transient_connection_errors(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_post(url, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise requests.ConnectionError("catalog is starting")
        return FakeResponse()

    monkeypatch.setattr("tender_parser.supplier_search.requests.post", fake_post)
    monkeypatch.setattr("tender_parser.supplier_search.time.sleep", sleeps.append)
    gateway = ClimateProductApiGateway(
        "https://climate.example/search",
        "x" * 32,
        retry_backoff_seconds=0.5,
    )

    total, _ = gateway.search("Royal Clima")

    assert total == 1
    assert calls == 3
    assert sleeps == [0.5, 1.0]


def test_climate_ssh_gateway_reads_last_json_line(monkeypatch, tmp_path) -> None:
    key = tmp_path / "climate_key"
    key.write_text("test", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        payload = FakeResponse().json()
        return SimpleNamespace(returncode=0, stdout="breez: loaded\n" + json.dumps(payload), stderr="")

    monkeypatch.setattr("tender_parser.supplier_search.subprocess.run", fake_run)
    gateway = ClimateProductSshGateway(
        "root@example.test", str(key), ssh_port=2222,
        ssh_bind_address="192.168.88.26",
    )
    total, products = gateway.search("Royal Clima", limit=50)

    assert total == 1
    assert products[0].supplier_name == "Бриз"
    assert captured["command"][0] == "ssh"
    assert captured["command"][captured["command"].index("-p") + 1] == "2222"
    assert captured["command"][captured["command"].index("-b") + 1] == "192.168.88.26"
    assert "Royal Clima" not in captured["command"][-1]
    assert "tender_catalog_cli search" in captured["command"][-1]
    assert captured["timeout"] == 180


def test_environment_prefers_ssh_over_http(monkeypatch, tmp_path) -> None:
    key = tmp_path / "climate_key"
    key.write_text("test", encoding="utf-8")
    monkeypatch.setenv("TENDER_CLIMATE_SSH_HOST", "root@example.test")
    monkeypatch.setenv("TENDER_CLIMATE_SSH_KEY", str(key))
    monkeypatch.setenv("TENDER_CLIMATE_SSH_PORT", "2222")
    monkeypatch.setenv("TENDER_CLIMATE_SSH_BIND_ADDRESS", "192.168.88.26")
    monkeypatch.setenv("TENDER_CLIMATE_API_URL", "https://unused.example/search")
    monkeypatch.setenv("TENDER_CLIMATE_API_TOKEN", "x" * 32)
    gateway = climate_gateway_from_environment()
    assert isinstance(gateway, ClimateProductSshGateway)
    assert gateway.ssh_port == 2222
    assert gateway.ssh_bind_address == "192.168.88.26"


def test_private_price_gateway_uses_general_price_command(monkeypatch, tmp_path) -> None:
    key = tmp_path / "catalog_key"
    key.write_text("test", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout=json.dumps(FakeResponse().json()), stderr="")

    monkeypatch.setattr("tender_parser.supplier_search.subprocess.run", fake_run)
    gateway = PrivatePriceSshGateway("root@example.test", str(key))
    gateway.search("шина автомобильная", limit=10)
    assert "search-prices" in captured["command"][-1]
