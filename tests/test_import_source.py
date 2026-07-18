from pathlib import Path

from openpyxl import Workbook

from tender_parser.sources.imports import ImportFolderSource


def test_import_folder_source_reads_csv_export(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "eat.csv").write_text(
        "Название;Ссылка;Номер;Заказчик;Регион;Сумма;Срок подачи;Источник\n"
        "Поставка МФУ;https://example.test/1;EAT-1;Администрация;Симферополь;45000;01.07.2026 10:00;ЕАТ\n",
        encoding="utf-8",
    )

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert len(result.tenders) == 1
    tender = result.tenders[0]
    assert tender.title == "Поставка МФУ"
    assert tender.source == "import-eat"
    assert tender.tender_number == "EAT-1"
    assert tender.region == "Симферополь"
    assert tender.price == 45_000.0
    assert tender.deadline.year == 2026
    assert tender.detail_status == "imported"
    assert tender.source_confidence == 0.8
    assert result.health[0].status == "ok"
    assert result.health[0].found == 1


def test_import_folder_source_reads_xlsx_export(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Предмет закупки", "URL", "НМЦК", "Дата окончания", "Площадка"])
    sheet.append(["Обслуживание кондиционеров", "https://example.test/2", 120000, "02.07.2026", "RTS"])
    workbook.save(imports_dir / "rts.xlsx")

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert len(result.tenders) == 1
    tender = result.tenders[0]
    assert tender.title == "Обслуживание кондиционеров"
    assert tender.source == "import-rts"
    assert tender.price == 120_000.0
    assert tender.deadline.day == 2


def test_import_folder_source_reads_xml_export(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "b2b.xml").write_text(
        """
        <root>
          <tender>
            <title>Поставка картриджей</title>
            <url>https://example.test/3</url>
            <number>B2B-3</number>
            <region>Севастополь</region>
            <price>99000</price>
            <deadline>03.07.2026 11:00</deadline>
            <source>B2B-Center</source>
          </tender>
        </root>
        """,
        encoding="utf-8",
    )

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert len(result.tenders) == 1
    tender = result.tenders[0]
    assert tender.title == "Поставка картриджей"
    assert tender.source == "import-b2b-center"
    assert tender.region == "Севастополь"
    assert tender.price == 99_000.0


def test_import_folder_source_skips_bad_row_and_keeps_good_ones(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "mix.csv").write_text(
        "Название;Ссылка\n"
        "Поставка МФУ;https://example.test/1\n"
        ";https://example.test/2\n"
        "Поставка кондиционеров;https://example.test/3\n",
        encoding="utf-8",
    )

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert len(result.tenders) == 2
    assert result.health[0].status == "partial"
    assert "mix.csv" in result.health[0].detail


def test_import_folder_source_survives_broken_xlsx(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "broken.xlsx").write_text("not a zip at all", encoding="utf-8")
    (imports_dir / "good.csv").write_text(
        "Название;Ссылка\nПоставка МФУ;https://example.test/1\n", encoding="utf-8"
    )

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert len(result.tenders) == 1
    assert result.health[0].status == "partial"
    assert "broken.xlsx" in result.health[0].detail


def test_import_folder_source_strips_timezone_from_iso_deadline(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "tz.csv").write_text(
        "Название;Ссылка;Срок подачи\nПоставка МФУ;https://example.test/1;2026-07-20T10:00:00+03:00\n",
        encoding="utf-8",
    )

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert result.tenders[0].deadline is not None
    assert result.tenders[0].deadline.tzinfo is None


def test_import_folder_source_detects_delimiter_by_header(tmp_path: Path) -> None:
    imports_dir = tmp_path / "imports"
    imports_dir.mkdir()
    (imports_dir / "commas.csv").write_text(
        'Название,Ссылка\n"Перчатки; маски; халаты; бахилы; колпаки",https://example.test/1\n',
        encoding="utf-8",
    )

    result = ImportFolderSource(imports_dir).fetch_with_report([])

    assert len(result.tenders) == 1
    assert result.tenders[0].title.startswith("Перчатки")


def test_import_folder_source_reports_empty_when_folder_missing(tmp_path: Path) -> None:
    result = ImportFolderSource(tmp_path / "missing").fetch_with_report([])

    assert result.tenders == []
    assert result.health[0].status == "empty"
