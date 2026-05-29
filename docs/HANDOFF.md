# Handoff: RTS Tender Parser

Дата: 2026-05-29

## Состояние

Реализована публичная версия парсера закупок с рабочим fallback-источником Rostender и расширенным RTS-market охватом:

- CLI: `python -m tender_parser run`
- Windows launcher: `Запустить_парсер.bat`
- Source parser: `tender_parser/sources/rts.py`
- ETP GPB RSS parser: `tender_parser/sources/etp_gpb.py`
- Tender.Pro API parser: `tender_parser/sources/tender_pro.py`
- Rostender parser: `tender_parser/sources/rostender.py`
- Composite source: `tender_parser/sources/composite.py`
- Filters: `tender_parser/filters.py`
- Storage: `tender_parser/storage.py`
- Excel/JSON exports: `tender_parser/exporters/`
- Тесты: `tests/`
- Исследование ЭТП: `docs/etp_source_research_2026-05-29.md`
- Публичные endpoints настраиваются в `tender_parser/config.py` через `RTS_MARKET_ENDPOINTS`.
- Региональные endpoints могут задавать `region_hint`, чтобы закупки из региональной витрины не терялись из-за пустого региона в строке таблицы.
- Основной live-слой в обычном запуске - `EtpGpbRssSource`, `TenderProSource`, `RostenderSource`; RTS идет резервом и не запускается, если первый слой уже вернул карточки.

## Последняя проверка

Команды, которые использовались для проверки:

```powershell
python -m pytest -v
python -m tender_parser run
```

На live-проверке 2026-05-29 Rosatom публичный источник вернул captcha/rate-limit, региональные RTS endpoints таймаутились, общий RTS падал по SSL. Поэтому добавлен Rostender fallback. ЭТП ГПБ RSS также таймаутится из текущей сети, но источник подключен с коротким timeout и не блокирует дальнейший сбор. Tender.Pro API отвечает стабильно и добавляет открытые процедуры в общий поток.

Последний live-запуск 2026-05-29:

- `Найдено`: 564
- `Подходящие`: 13
- `На проверку`: 6
- `Отсеянные`: 545
- `latest.json`: 19 actionable, из них 18 Rostender и 1 Tender.Pro

Созданы/обновлены локальные артефакты:

- `data/tenders.db`
- `exports/latest.json`
- `exports/tenders_2026-05-29.xlsx`

## Как продолжать

1. Добавить B2B-Center через публичный HTML или личный кабинет; публичная страница сейчас может показывать anti-bot/rate-limit.
2. Добавить Торги82 HTML как регионально важный источник для Крыма.
3. Проверить, можно ли стабилизировать ЭТП ГПБ через другой домен/API или личный кабинет, потому что RSS-документация есть, но live-запросы таймаутятся.
4. После этого смотреть Фабрикант и ОТС.
5. Для CRM лучше сначала читать `exports/latest.json`; сейчас там подходящие и `review`-кандидаты.
6. При изменении словарей править `tender_parser/config.py` и добавлять focused tests в `tests/test_filters.py`.

## Git

Рабочая ветка: `codex/rts-tender-parser`.

Перед передачей дальше проверить:

```powershell
git status --short
python -m pytest -v
```
