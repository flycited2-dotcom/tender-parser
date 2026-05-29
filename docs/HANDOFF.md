# Handoff: RTS Tender Parser

Дата: 2026-05-29

## Состояние

Реализована публичная версия парсера закупок с рабочим fallback-источником Rostender и расширенным RTS-market охватом:

- CLI: `python -m tender_parser run`
- Windows launcher: `Запустить_парсер.bat`
- Source parser: `tender_parser/sources/rts.py`
- Rostender parser: `tender_parser/sources/rostender.py`
- Composite source: `tender_parser/sources/composite.py`
- Filters: `tender_parser/filters.py`
- Storage: `tender_parser/storage.py`
- Excel/JSON exports: `tender_parser/exporters/`
- Тесты: `tests/`
- Исследование ЭТП: `docs/etp_source_research_2026-05-29.md`
- Публичные endpoints настраиваются в `tender_parser/config.py` через `RTS_MARKET_ENDPOINTS`.
- Региональные endpoints могут задавать `region_hint`, чтобы закупки из региональной витрины не терялись из-за пустого региона в строке таблицы.
- Основной live-источник в обычном запуске - `RostenderSource`; RTS идет вторым и не запускается, если Rostender уже вернул карточки.

## Последняя проверка

Команды, которые использовались для проверки:

```powershell
python -m pytest -v
python -m tender_parser run
```

На live-проверке 2026-05-29 Rosatom публичный источник вернул captcha/rate-limit, региональные RTS endpoints таймаутились, общий RTS падал по SSL. Поэтому добавлен Rostender fallback.

Последний live-запуск 2026-05-29:

- `Найдено`: 361
- `Подходящие`: 17
- `На проверку`: 7
- `Отсеянные`: 337

Созданы/обновлены локальные артефакты:

- `data/tenders.db`
- `exports/latest.json`
- `exports/tenders_2026-05-29.xlsx`

## Как продолжать

1. Добавить источник ЭТП ГПБ через RSS/API: это самый чистый следующий источник по исследованию.
2. Добавить B2B-Center через публичный HTML; параллельно проверить API-документацию в личном кабинете.
3. Проверить Tender.Pro API на возможность чтения открытых процедур без платного ключа; если работает, сделать отдельный source.
4. Добавить Торги82 HTML как регионально важный источник для Крыма.
5. Для CRM лучше сначала читать `exports/latest.json`; сейчас там подходящие и `review`-кандидаты.
6. При изменении словарей править `tender_parser/config.py` и добавлять focused tests в `tests/test_filters.py`.

## Git

Рабочая ветка: `codex/rts-tender-parser`.

Перед передачей дальше проверить:

```powershell
git status --short
python -m pytest -v
```
