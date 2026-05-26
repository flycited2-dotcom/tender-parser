# Handoff: RTS Tender Parser

Дата: 2026-05-26

## Состояние

Реализована первая рабочая версия парсера RTS-Tender:

- CLI: `python -m tender_parser run`
- Windows launcher: `Запустить_парсер.bat`
- Source parser: `tender_parser/sources/rts.py`
- Filters: `tender_parser/filters.py`
- Storage: `tender_parser/storage.py`
- Excel/JSON exports: `tender_parser/exporters/`
- Тесты: `tests/`

## Последняя проверка

Команды, которые использовались для проверки:

```powershell
python -m pytest -v
python -m tender_parser run
```

На последнем live-запуске публичный источник вернул `0` найденных процедур по текущим ключевым словам и фильтру `price_start=30000`, но pipeline завершился без ошибки и создал:

- `data/tenders.db`
- `exports/latest.json`
- `exports/tenders_2026-05-26.xlsx`

## Как продолжать

1. Если публичный RTS-Rosatom дает мало результатов, добавить второй источник или раздел RTS в `tender_parser/sources/`.
2. Если нужна авторизация, добавлять ее отдельным source-модулем, не смешивая с текущим публичным парсером.
3. Для CRM лучше сначала читать `exports/latest.json`; позже можно добавить API sender отдельным exporter/service.
4. При изменении словарей править `tender_parser/config.py` и добавлять focused tests в `tests/test_filters.py`.

## Git

Рабочая ветка: `codex/rts-tender-parser`.

Перед передачей дальше проверить:

```powershell
git status --short
python -m pytest -v
```
