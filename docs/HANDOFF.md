# Handoff: RTS Tender Parser

Дата: 2026-06-21

## Состояние

Реализована операционная версия парсера закупок: сбор, health-report источников, дедупликация, ежедневная CRM-очередь и подготовка запуска по расписанию.

- CLI: `python -m tender_parser run`
- Windows launcher: `Запустить_парсер.bat`
- Source parser: `tender_parser/sources/rts.py`
- ETP GPB RSS parser: `tender_parser/sources/etp_gpb.py`
- Tender.Pro API parser: `tender_parser/sources/tender_pro.py`
- Torgi82 JSON parser: `tender_parser/sources/torgi82.py`
- EAT/Berezka integration API parser: `tender_parser/sources/eat.py`
- EIS/zakupki.gov.ru parser: `tender_parser/sources/eis.py`
- Rostender parser: `tender_parser/sources/rostender.py`
- Composite source: `tender_parser/sources/composite.py`
- Source health types: `tender_parser/run_report.py`
- Cross-source deduplication: `tender_parser/dedup.py`
- Filters: `tender_parser/filters.py`
- Storage: `tender_parser/storage.py`
- Excel/JSON exports: `tender_parser/exporters/`
- Тесты: `tests/`
- Исследование ЭТП: `docs/etp_source_research_2026-05-29.md`
- Публичные endpoints настраиваются в `tender_parser/config.py` через `RTS_MARKET_ENDPOINTS`.
- Региональные endpoints могут задавать `region_hint`, чтобы закупки из региональной витрины не терялись из-за пустого региона в строке таблицы.
- Основной live-слой в обычном запуске - `EtpGpbRssSource`, `TenderProSource`, `Torgi82Source`, `B2BCenterSource`, `EatIntegrationSource`, `EisZakupkiSource`, `RostenderSource`; RTS идет резервом и не запускается, если первый слой уже вернул карточки.
- `CompositeSource.fetch_with_report` собирает `ok`/`empty`/`skipped`/`error` для каждого источника и длительность запроса. Результат пишется в `exports/run_report.json`.
- Высокоуверенные дубли ЕИС/Rostender склеиваются до фильтрации; приоритет у ЕИС. Перед хранением `TenderStorage` возвращает впервые увиденные карточки, из которых формируется `exports/new_tenders.json`.
- Excel теперь начинается с листа `Новые`. Для фонового запуска есть `run_tender_parser_silent.bat`; `Настроить_ежедневный_запуск.ps1 -Time "08:00"` создает задачу Windows Task Scheduler.
- `TenderRecord.match_confidence` разделяет карточки на `точное`, `вероятное` и `ручная проверка`; поле экспортируется в Excel, `latest.json` и `new_tenders.json`.
- Карточка без срока подачи больше не теряется автоматически: при подтвержденных товаре, регионе и сумме она попадает в `вероятное`.
- Добавлен `B2BCenterSource`, который читает публичную таблицу B2B-Center по 16 товарным запросам. В карточке списка обычно нет цены и региона, поэтому она попадает в `ручная проверка` до уточнения по первоисточнику.
- ЕИС, ЭТП ГПБ и Rostender используют общую матрицу из 80 запросов: 16 товарных групп x Симферополь, Севастополь, Крым, Запорожская и Херсонская области.
- 2026-06-21 задача `Tender Parser Daily` создана и проверена в Windows Task Scheduler: ежедневно в 08:00, launcher - `run_tender_parser_silent.bat`, состояние `Ready`.
- `EatIntegrationSource` активируется только при наличии `EAT_API_TOKEN` и `EAT_EXT_SYSTEM`. Без них источник отдает `SourceFetchError`, composite идет дальше.
- ЕИС/`zakupki.gov.ru` добавлен как главный широкий источник для 44-ФЗ/223-ФЗ. Отдельные ЭТП остаются дополнительными каналами для коммерческих, малых и региональных закупок.
- `EisZakupkiSource` отключает `session.trust_env`, потому что системный proxy в текущей среде приводил к долгим таймаутам на `zakupki.gov.ru`.

## Последняя проверка

Команды, которые использовались для проверки:

```powershell
python -m pytest -v
python -m tender_parser run
```

На live-проверке 2026-05-29 Rosatom публичный источник вернул captcha/rate-limit, региональные RTS endpoints таймаутились, общий RTS падал по SSL. Поэтому добавлен Rostender fallback. ЭТП ГПБ RSS также таймаутится из текущей сети, но источник подключен с коротким timeout и не блокирует дальнейший сбор. Tender.Pro API отвечает стабильно и добавляет открытые процедуры в общий поток. Торги82 подключен через `https://etp.torgi82.ru/searchServlet`; endpoint отдает JSON последних 20 процедур.

Последний live-запуск 2026-06-21 после операционного релиза v2:

- `Найдено`: 585
- `После дедупликации`: 573
- `Подходящие`: 27
- `На проверку`: 11
- `Отсеянные`: 535
- `Новые для CRM`: 37
- `latest.json`: 38 actionable, из них 34 Rostender, 3 Торги82, 1 Tender.Pro
- `run_report.json`: ЭТП ГПБ - timeout, ЕАТ - пропущен без токена, ЕИС - timeout, Tender.Pro/Торги82/Rostender - OK.

Допроверка 2026-05-30:

- ЕАТ/Березка: главная страница отвечает captcha, но официальная инструкция интеграции подтверждает REST/XML endpoints. `requestOrderList` без токена возвращает `401`, публичные классификаторы скачиваются. Кодовая заготовка добавлена, нужен токен из ЛК.
- ЕИС/zakupki.gov.ru: публичный поиск `/epz/order/extendedsearch/results.html` работает и отдает карточки закупок. При обычном `requests.Session` были таймауты из-за системного proxy; при `trust_env=False` источник собирает около 86 карточек за 10 секунд на текущем наборе запросов.
- Сбербанк-АСТ: `www.sberbank-ast.ru` и `utp.sberbank-ast.ru` таймаутятся из текущей сети. `world.sberbank-ast.ru` открывает публичный Procurement list, но это не основной российский реестр.
- ЗаказРФ: инструкции подтверждают `Поиск торгов`, но live-домены `www`, `etp`, `223etp`, `bp` таймаутятся; публичный API не найден.

Созданы/обновлены локальные артефакты:

- `data/tenders.db`
- `exports/latest.json`
- `exports/tenders_2026-05-30.xlsx`

## Как продолжать

Следующий практический уровень охвата: **альтернативный официальный канал ЕИС, затем ЕАТ по токену и B2B-Center**.

1. Проверить альтернативный официальный канал ЕИС: XML/open-data или кабинетный экспорт, потому что HTML search может таймаутиться. Аналогично проверить другой endpoint ЭТП ГПБ.
2. Получить в ЛК ЕАТ токен и код внешней системы, затем проверить `EatIntegrationSource` live-запуском.
3. Добавить B2B-Center через публичный HTML или личный кабинет; публичная страница сейчас может показывать anti-bot/rate-limit.
4. Углубить Торги82: найти корректный GWT/searchServlet payload для ключевых слов и пагинации, потому что простой endpoint сейчас дает только последние 20 процедур.
5. После этого смотреть Фабрикант, ОТС и OnlineContract.
6. Для CRM читать `exports/new_tenders.json` как дельту, `exports/latest.json` как полную очередь и `exports/run_report.json` как диагностику.
7. При изменении словарей править `tender_parser/config.py` и добавлять focused tests в `tests/test_filters.py`.

## Git

Рабочая ветка: `codex/rts-tender-parser`.

Перед передачей дальше проверить:

```powershell
git status --short
python -m pytest -v
```
