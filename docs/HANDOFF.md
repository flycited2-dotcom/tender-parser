# Handoff: RTS Tender Parser

Дата: 2026-05-30

## Состояние

Реализована публичная версия парсера закупок с рабочим fallback-источником Rostender и расширенным RTS-market охватом:

- CLI: `python -m tender_parser run`
- Windows launcher: `Запустить_парсер.bat`
- Source parser: `tender_parser/sources/rts.py`
- ETP GPB RSS parser: `tender_parser/sources/etp_gpb.py`
- Tender.Pro API parser: `tender_parser/sources/tender_pro.py`
- Torgi82 JSON parser: `tender_parser/sources/torgi82.py`
- EAT/Berezka integration API parser: `tender_parser/sources/eat.py`
- Rostender parser: `tender_parser/sources/rostender.py`
- Composite source: `tender_parser/sources/composite.py`
- Filters: `tender_parser/filters.py`
- Storage: `tender_parser/storage.py`
- Excel/JSON exports: `tender_parser/exporters/`
- Тесты: `tests/`
- Исследование ЭТП: `docs/etp_source_research_2026-05-29.md`
- Публичные endpoints настраиваются в `tender_parser/config.py` через `RTS_MARKET_ENDPOINTS`.
- Региональные endpoints могут задавать `region_hint`, чтобы закупки из региональной витрины не терялись из-за пустого региона в строке таблицы.
- Основной live-слой в обычном запуске - `EtpGpbRssSource`, `TenderProSource`, `Torgi82Source`, `EatIntegrationSource`, `RostenderSource`; RTS идет резервом и не запускается, если первый слой уже вернул карточки.
- `EatIntegrationSource` активируется только при наличии `EAT_API_TOKEN` и `EAT_EXT_SYSTEM`. Без них источник отдает `SourceFetchError`, composite идет дальше.

## Последняя проверка

Команды, которые использовались для проверки:

```powershell
python -m pytest -v
python -m tender_parser run
```

На live-проверке 2026-05-29 Rosatom публичный источник вернул captcha/rate-limit, региональные RTS endpoints таймаутились, общий RTS падал по SSL. Поэтому добавлен Rostender fallback. ЭТП ГПБ RSS также таймаутится из текущей сети, но источник подключен с коротким timeout и не блокирует дальнейший сбор. Tender.Pro API отвечает стабильно и добавляет открытые процедуры в общий поток. Торги82 подключен через `https://etp.torgi82.ru/searchServlet`; endpoint отдает JSON последних 20 процедур.

Последний live-запуск 2026-05-30 после добавления заготовки ЕАТ:

- `Найдено`: 581
- `Подходящие`: 13
- `На проверку`: 7
- `Отсеянные`: 561
- `latest.json`: 20 actionable, из них 19 Rostender и 1 Tender.Pro

Допроверка 2026-05-30:

- ЕАТ/Березка: главная страница отвечает captcha, но официальная инструкция интеграции подтверждает REST/XML endpoints. `requestOrderList` без токена возвращает `401`, публичные классификаторы скачиваются. Кодовая заготовка добавлена, нужен токен из ЛК.
- Сбербанк-АСТ: `www.sberbank-ast.ru` и `utp.sberbank-ast.ru` таймаутятся из текущей сети. `world.sberbank-ast.ru` открывает публичный Procurement list, но это не основной российский реестр.
- ЗаказРФ: инструкции подтверждают `Поиск торгов`, но live-домены `www`, `etp`, `223etp`, `bp` таймаутятся; публичный API не найден.

Созданы/обновлены локальные артефакты:

- `data/tenders.db`
- `exports/latest.json`
- `exports/tenders_2026-05-30.xlsx`

## Как продолжать

1. Добавить B2B-Center через публичный HTML или личный кабинет; публичная страница сейчас может показывать anti-bot/rate-limit.
2. Углубить Торги82: найти корректный GWT/searchServlet payload для ключевых слов и пагинации, потому что простой endpoint сейчас дает только последние 20 процедур.
3. Получить в ЛК ЕАТ токен и код внешней системы, затем проверить `EatIntegrationSource` live-запуском.
4. Проверить, можно ли стабилизировать ЭТП ГПБ через другой домен/API или личный кабинет, потому что RSS-документация есть, но live-запросы таймаутятся.
5. После этого смотреть Фабрикант и ОТС.
6. Для CRM лучше сначала читать `exports/latest.json`; сейчас там подходящие и `review`-кандидаты.
7. При изменении словарей править `tender_parser/config.py` и добавлять focused tests в `tests/test_filters.py`.

## Git

Рабочая ветка: `codex/rts-tender-parser`.

Перед передачей дальше проверить:

```powershell
git status --short
python -m pytest -v
```
