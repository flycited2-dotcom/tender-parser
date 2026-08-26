from tender_parser.regions import region_priority_rank


def test_region_priority_groups_follow_requested_logistics_order() -> None:
    assert region_priority_rank("Севастополь") == 0
    assert region_priority_rank("Республика Крым") == 0
    assert region_priority_rank("Запорожская область") == 1
    assert region_priority_rank("Херсонская область") == 1
    assert region_priority_rank("Московская область") == 2
    assert region_priority_rank(None) == 3
