from tender_parser.product_intelligence import (
    alternative_search_links,
    build_search_queries,
    classify_item_kind,
    extract_device_models,
    oem_references,
)


def test_brother_drum_search_uses_model_synonyms_and_oem_part() -> None:
    queries = build_search_queries(
        "Блок фотобарабана 26.20.40.120",
        "Совместимость с принтером Brother HL-L5210DW: Да; Ресурс ≥ 57 000 листов",
    )

    assert queries[0] == "Фотобарабан HL-L5210DW"
    assert "Драм-юнит HL-L5210DW" in queries
    assert "Фотобарабан DR3600" in queries
    assert "DR3600" in queries


def test_toner_chip_maps_11000_page_brother_model_to_tn3600xxl() -> None:
    references = oem_references(
        "Чип для тонер-картриджа",
        "Brother HL-L5210DW; ресурс чипа ≥ 11 000 листов",
    )

    assert classify_item_kind("Чип для тонер-картриджа") == "toner_chip"
    assert references[0].parts[0] == "TN3600XXL"


def test_models_and_alternative_links_are_reusable() -> None:
    specs = "Kyocera ECOSYS M2040dn, Kyocera ECOSYS M2135dn"

    assert extract_device_models(specs) == ("M2040dn", "M2135dn")
    links = alternative_search_links("Термопленка", specs)
    assert links[0]["label"] == "Яндекс"
    assert "search" in links[0]["url"]
