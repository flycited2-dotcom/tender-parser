from datetime import datetime

from tender_parser import config
from tender_parser.filters import evaluate_tender, target_region
from tender_parser.models import TenderRecord


NOW = datetime(2026, 5, 19, 12, 0)


def make_tender(**overrides: object) -> TenderRecord:
    data = {
        "title": "Поставка МФУ в Республику Крым",
        "url": "https://example.test/tender-1/",
        "source": "test",
        "tender_number": "1",
        "customer": "Заказчик",
        "region": "Республика Крым",
        "price": 45_000.0,
        "deadline": datetime(2026, 5, 25, 10, 0),
        "raw_text": "Поставка МФУ в Республику Крым",
    }
    data.update(overrides)
    return TenderRecord(**data)


def test_evaluate_tender_matches_region_category_price_and_deadline() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.category == "Компьютерная техника и периферия"
    assert "регион: республика крым" in result.include_reason.lower()
    assert "мфу" in result.include_reason.lower()


def test_evaluate_tender_marks_verified_card_exact() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.match_confidence == "точное"


def test_evaluate_tender_marks_exact_match_hot() -> None:
    result = evaluate_tender(make_tender(), now=NOW)

    assert result.filter_status == "matched"
    assert result.review_priority == "hot"


def test_evaluate_tender_excludes_stop_terms() -> None:
    result = evaluate_tender(make_tender(title="Поставка лекарственных препаратов МФУ"), now=NOW)

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason
    assert "лекарств" in result.exclude_reason


def test_target_region_is_retained_for_medical_and_other_excluded_topics() -> None:
    tender = make_tender(
        title="Поставка лекарственных препаратов",
        raw_text="Поставка лекарственных препаратов в Республику Крым",
    )

    assert evaluate_tender(tender, now=NOW).review_priority == "excluded"
    assert target_region(tender) == "Республика Крым"


def test_evaluate_tender_excludes_low_price() -> None:
    result = evaluate_tender(make_tender(price=29_999.0), now=NOW)

    assert result.filter_status == "excluded"
    assert "меньше 30000" in result.exclude_reason


def test_evaluate_tender_excludes_expired_deadline() -> None:
    result = evaluate_tender(make_tender(deadline=datetime(2026, 5, 18, 23, 59)), now=NOW)

    assert result.filter_status == "excluded"
    assert "срок подачи истек" in result.exclude_reason


def test_evaluate_tender_reviews_missing_region_for_interesting_category() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ", region=None, raw_text="Поставка МФУ"),
        now=NOW,
    )

    assert result.filter_status == "review"
    assert result.category == "Компьютерная техника и периферия"
    assert result.match_confidence == "ручная проверка"
    assert "регион не найден" in result.exclude_reason


def test_evaluate_tender_reviews_unknown_deadline_as_probable() -> None:
    result = evaluate_tender(make_tender(deadline=None), now=NOW)

    assert result.filter_status == "review"
    assert result.match_confidence == "вероятное"
    assert result.review_priority == "review"
    assert result.exclude_reason == "требуется проверка: срок подачи не указан"


def test_evaluate_tender_marks_missing_deadline_as_review_priority() -> None:
    result = evaluate_tender(make_tender(deadline=None), now=NOW)

    assert result.filter_status == "review"
    assert result.match_confidence == "вероятное"
    assert result.review_priority == "review"


def test_evaluate_tender_excludes_unscoped_b2b_without_target_region() -> None:
    result = evaluate_tender(
        make_tender(
            source="b2b-center",
            title="Поставка кондиционеров",
            region=None,
            price=None,
            raw_text="Поставка кондиционеров",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.exclude_reason == "целевой регион не подтвержден"


def test_evaluate_tender_keeps_b2b_when_delivery_to_target_is_in_text() -> None:
    result = evaluate_tender(
        make_tender(
            source="b2b-center",
            title="Поставка серверного оборудования",
            region=None,
            raw_text="Заказчик находится в Москве. Адрес места поставки: г. Севастополь.",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert "регион: севастополь" in result.include_reason.lower()


def test_evaluate_tender_keeps_target_delivery_over_customer_region_field() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка МФУ",
            region="г. Москва",
            raw_text="Заказчик: Москва. Адрес поставки: Республика Крым, г. Ялта.",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert (
        "регион: республика крым" in result.include_reason.lower()
        or "регион: крым" in result.include_reason.lower()
    )


def test_evaluate_tender_excludes_explicit_non_target_delivery_without_region_field() -> None:
    result = evaluate_tender(
        make_tender(
            source="test",
            title="Поставка офисной бумаги",
            region=None,
            raw_text="Доставка до склада заказчика в г. Магадан.",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.exclude_reason == "регион не целевой: магадан"


def test_evaluate_tender_does_not_let_page_boilerplate_override_declared_region() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка металлических шкафов",
            region="Амурская область",
            raw_text=(
                "ГАУ Амурской области. Фильтр регионов: Республика Крым, "
                "Севастополь, Запорожская область, Херсонская область"
            ),
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.exclude_reason == "регион не целевой"


def test_evaluate_tender_excludes_known_non_target_region() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка сейфов", region="Москва", raw_text="Поставка сейфов"),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.exclude_reason == "регион не целевой"


def test_evaluate_tender_reviews_missing_price_for_interesting_category_and_region() -> None:
    result = evaluate_tender(make_tender(price=None), now=NOW)

    assert result.filter_status == "review"
    assert result.category == "Компьютерная техника и периферия"
    assert "сумма не указана" in result.exclude_reason


def test_evaluate_tender_does_not_treat_monitoring_as_monitor() -> None:
    result = evaluate_tender(
        make_tender(
            title="Мониторинг рынка цен в Республике Крым",
            raw_text="Мониторинг рынка цен в Республике Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_matches_pipeline_as_plumbing() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка трубопроводов из стали в Республику Крым",
            raw_text="Поставка трубопроводов из стали в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Отопление, сантехника и насосы"


def test_evaluate_tender_excludes_filter_cartridges() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка фильтровальных картриджей в Республику Крым",
            raw_text="Поставка фильтровальных картриджей в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_medical_bicarbonate_cartridges() -> None:
    result = evaluate_tender(
        make_tender(
            title="Картриджи бикарбонатные и концентраты кислотные",
            raw_text="Картриджи бикарбонатные и концентраты кислотные в Республику Крым",
            region="Крым",
            price=1_500_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_lab_consumables_even_with_office_word() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка расходных материалов для клинико-диагностической лаборатории",
            raw_text="Поставка ручек-скарификаторов и расходных материалов для лаборатории в Севастополь",
            region="Севастополь",
            price=500_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.review_priority == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_generic_consumables_without_target_context() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка расходных материалов",
            raw_text="Поставка расходных материалов в Республику Крым",
            region="Республика Крым",
            price=120_000.0,
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert result.review_priority == "excluded"


def test_evaluate_tender_does_not_treat_arsenic_as_mouse() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка соединений мышьяка в Республику Крым",
            raw_text="Поставка соединений мышьяка в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_does_not_treat_thyroid_as_switchboard() -> None:
    result = evaluate_tender(
        make_tender(
            title="УЗИ щитовидной железы в Республике Крым",
            raw_text="УЗИ щитовидной железы",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"


def test_evaluate_tender_matches_actual_monitor_word() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка мониторов в Республику Крым",
            raw_text="Поставка мониторов в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Компьютерная техника и периферия"
    assert "монитор" in result.include_reason.lower()


def test_evaluate_tender_matches_rts_electrical_equipment_terms() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка разъединителей и изоляторов 10 кВ",
            raw_text="Разъединитель РВЗ 10 кВ, изоляторы, трансформатор тока 35кВ",
            region="Респ. Крым",
            price=None,
        ),
        now=NOW,
    )

    assert result.filter_status == "review"
    assert result.category == "Электротехника и оборудование"
    assert result.review_priority == "review"
    assert "разъединитель" in result.include_reason.lower()


def test_evaluate_tender_keeps_crimean_city_region() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ", region="г. Ялта", raw_text="Поставка МФУ"),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert "регион: крым" in result.include_reason.lower()


def test_evaluate_tender_ignores_stop_terms_in_customer_name() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка МФУ в Республику Крым",
            customer="ГБУЗ Клинико-диагностическая лаборатория",
            raw_text="Поставка МФУ в Республику Крым ГБУЗ Клинико-диагностическая лаборатория",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.review_priority == "hot"


def test_evaluate_tender_matches_region_from_title() -> None:
    result = evaluate_tender(
        make_tender(title="Поставка МФУ в Севастополь", region=None, raw_text="Поставка МФУ"),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert "регион: севастополь" in result.include_reason.lower()


def test_evaluate_tender_matches_household_chemistry_and_cleaning() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка бытовой химии и моющих средств",
            raw_text="Поставка бытовой химии и моющих средств в Республику Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Хозяйственные товары и уборка"


def test_evaluate_tender_matches_cleaning_supplies() -> None:
    result = evaluate_tender(
        make_tender(
            title="Хозяйственные товары и уборочный инвентарь",
            raw_text="Хозяйственные товары, салфетки, мешки для мусора",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Хозяйственные товары и уборка"


def test_evaluate_tender_matches_hotel_supplies() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка постельного белья и полотенец для гостиницы",
            raw_text="Постельное белье, полотенца, подушки, одеяла",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Гостиничное и хозяйственное обеспечение"


def test_evaluate_tender_matches_large_appliances() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка бойлеров и морозильных камер",
            raw_text="Бойлер, морозильная камера, электрическая плита",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Бытовая техника"


def test_evaluate_tender_matches_multiword_terms_in_oblique_cases() -> None:
    network = evaluate_tender(
        make_tender(
            title="Поставка сетевого оборудования в Республику Крым",
            raw_text="Поставка сетевого оборудования",
        ),
        now=NOW,
    )
    stationery = evaluate_tender(
        make_tender(
            title="Поставка канцелярских товаров в Севастополь",
            raw_text="Поставка канцелярских товаров",
        ),
        now=NOW,
    )

    assert network.filter_status == "matched"
    assert network.category == "Компьютерная техника и периферия"
    assert stationery.filter_status == "matched"
    assert stationery.category == "Канцелярия и офис"


def test_evaluate_tender_excludes_stop_phrases_in_oblique_cases() -> None:
    result = evaluate_tender(
        make_tender(
            title="Выполнение капитального ремонта здания котельной со светильниками",
            raw_text="Выполнение капитального ремонта здания",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_sale_listings() -> None:
    result = evaluate_tender(
        make_tender(
            title='АО "РУСАЛ" реализует б/у мониторы со склада в Республике Крым',
            raw_text="реализует б/у мониторы",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_matches_measuring_devices_and_batteries() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка вольтметров и аккумуляторных батарей",
            raw_text="Вольтметр трехфазный, батарея аккумуляторная свинцово-кислотная",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Резервное электропитание и ИБП"


def test_evaluate_tender_ignores_boilerplate_provoditsya() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка щебня фракции 20-40",
            raw_text="Аукцион проводится в электронной форме. Место поставки: г. Симферополь",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_matches_paper_in_genitive() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка бумаги офисной формата А4",
            raw_text="Поставка бумаги офисной, Республика Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"
    assert result.category == "Канцелярия и офис"


def test_evaluate_tender_removes_customer_case_insensitively() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка мониторов",
            customer='ГБУЗ "Клинико-диагностическая лаборатория"',
            raw_text='Заказчик: ГБУЗ "КЛИНИКО-ДИАГНОСТИЧЕСКАЯ ЛАБОРАТОРИЯ". Поставка мониторов, Симферополь',
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"


def test_evaluate_tender_does_not_stop_realizuetsya() -> None:
    result = evaluate_tender(
        make_tender(
            title="Закупка мониторов",
            raw_text="Закупка мониторов. Закупка реализуется в соответствии с 44-ФЗ, Республика Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "matched"


def test_evaluate_tender_stops_laboratory_in_genitive() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка реагентов для лаборатории",
            raw_text="Поставка реагентов для лаборатории, холодильник фармацевтический",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"


def test_evaluate_tender_stops_laboratory_adjectives() -> None:
    result = evaluate_tender(
        TenderRecord(
            title="Мебель специализированная лабораторная",
            url="https://example.test/lab-furniture",
            source="test",
            region="Республика Крым",
            price=250_000,
            deadline=datetime(2026, 6, 1),
            raw_text="Поставка шкафов и лабораторной мебели",
        ),
        now=datetime(2026, 5, 19),
    )

    assert result.filter_status == "excluded"
    assert "лабораторн" in result.exclude_reason


def test_evaluate_tender_stops_neurosurgery_consumables_matching_wire() -> None:
    result = evaluate_tender(
        TenderRecord(
            title="Расходные материалы для отделения нейрохирургии: проводник ручной",
            url="https://example.test/neurosurgery",
            source="test",
            region="Севастополь",
            price=1_228_000,
            deadline=datetime(2026, 6, 1),
            raw_text="Проводник для доступа к периферическим сосудам",
        ),
        now=datetime(2026, 5, 19),
    )

    assert result.filter_status == "excluded"
    assert "нейрохирург" in result.exclude_reason
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_ignores_category_words_in_customer_name() -> None:
    customer = "Департамент административно-хозяйственного обеспечения города Севастополя"
    result = evaluate_tender(
        make_tender(
            title="На поставку щебня",
            customer=customer,
            raw_text=f"На поставку щебня Севастополь {customer} электронный аукцион",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_excludes_insulin_supply_with_pen_injector() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка лекарственного препарата для медицинского применения Инсулин гларгин",
            raw_text="Инсулин гларгин раствор шприц-ручка 3 мл Республика Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_excludes_construction_and_installation_works() -> None:
    result = evaluate_tender(
        make_tender(
            title="Выполнение строительно-монтажных и пусконаладочных работ по объекту КЛ-6 кВ",
            raw_text="Строительно-монтажные работы, кабель КЛ-6 кВ, Севастополь",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_does_not_match_pen_after_hyphen() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка шприц-ручек для инъекций в Республику Крым",
            raw_text="Шприц-ручка для инъекций",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_evaluate_tender_excludes_medical_equipment() -> None:
    result = evaluate_tender(
        make_tender(
            title="Поставка медицинского оборудования в рамках федерального проекта",
            raw_text="Поставка медицинского оборудования, холодильник фармацевтический, мониторы пациента",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "стоп-тема" in result.exclude_reason


def test_evaluate_tender_still_excludes_medical_and_construction() -> None:
    medical = evaluate_tender(
        make_tender(
            title="Поставка медицинских изделий для больницы",
            raw_text="Поставка медицинских изделий",
        ),
        now=NOW,
    )
    construction = evaluate_tender(
        make_tender(
            title="Капитальный ремонт жилищного фонда",
            raw_text="Капитальный ремонт жилищного фонда",
        ),
        now=NOW,
    )

    assert medical.filter_status == "excluded"
    assert construction.filter_status == "excluded"


def test_evaluate_tender_clears_matched_metadata_when_excluded() -> None:
    enriched = make_tender(
        price=29_999.0,
        category="Компьютерная техника и периферия",
        include_reason="old include",
        matched_terms=["мфу"],
    )

    result = evaluate_tender(enriched, now=NOW)

    assert result.filter_status == "excluded"
    assert result.category is None
    assert result.include_reason == ""
    assert result.matched_terms == []


def test_medical_lung_ventilation_is_not_classified_as_hvac() -> None:
    result = evaluate_tender(
        make_tender(
            title=(
                "Датчик кислорода для системы искусственной "
                "вентиляции легких"
            ),
            raw_text="Аппарат ИВЛ, Севастополь",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "медицинская вентиляция" in result.exclude_reason


def test_anaesthesia_ventilator_is_not_classified_as_hvac() -> None:
    result = evaluate_tender(
        make_tender(
            title="Наркозно-дыхательный аппарат с вентиляцией",
            raw_text="Наркозно-дыхательный аппарат, Республика Крым",
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "медицинская вентиляция" in result.exclude_reason


def test_single_word_category_term_must_be_present_in_title() -> None:
    result = evaluate_tender(
        make_tender(
            title="Оказание услуг по уборке помещений",
            raw_text=(
                "Оказание услуг по уборке. Заказчик ранее покупал ИБП. "
                "Республика Крым"
            ),
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
    assert "категория интереса не найдена" in result.exclude_reason


def test_legal_software_boilerplate_does_not_create_it_match(monkeypatch) -> None:
    monkeypatch.setitem(
        config.CATEGORY_KEYWORDS,
        "Тест ИТ",
        ["программное обеспечение"],
    )
    result = evaluate_tender(
        make_tender(
            title="Поставка автомобильных шин",
            raw_text=(
                "Поставка автомобильных шин. Исключение: осуществляется "
                "закупка товаров, не относящихся к товарам и программному "
                "обеспечению, указанным в позициях 1-7. Республика Крым"
            ),
        ),
        now=NOW,
    )

    assert result.filter_status == "excluded"
