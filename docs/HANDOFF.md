# Handoff: RTS Tender Parser

## Обновление 2026-07-03

Выполнена фаза 1 программы качества парсинга (спека: `docs/superpowers/specs/2026-07-03-parsing-quality-improvements-design.md`, план: `docs/superpowers/plans/2026-07-03-parsing-quality-phase1.md`):

- Новый модуль `tender_parser/regions.py` — единый словарь регионов с городами (Ялта, Керчь, Евпатория, Феодосия, Мелитополь, Геническ и др.), падежами и сокращениями; используется фильтрами, ЕИС и дедупом. Карточка с регионом «г. Ялта» больше не исключается как «регион не целевой».
- Стоп-термы проверяются по предмету закупки (title + raw_text без имени заказчика): больница с «лабораторией» в названии, покупающая МФУ, больше не отсекается.
- `normalize_text` сводит `ё→е` и чистит узкие пробелы; `word_term_matches` с таблицей исключений (`монитор(?!инг)`, `мышь(?!як)`, `щит(?!овидн)`) общий для фильтров и документов.
- Кросс-источниковый дедуп в `CompositeSource` — по `unique_key` (`source:number`), совпадение номеров у разных ЭТП больше не выбрасывает тендер.
- `RtsPublicSource` обходит региональные endpoint'ы (с `region_hint`) первыми, дубль не теряет регион.
- `TenderStorage.upsert_many` не затирает заполненные customer/region/price/deadline/published_at/raw_text пустыми значениями и возвращает, кроме first-seen, карточки, впервые ставшие actionable (excluded → hot/review/wide) — CRM-дельта больше не пропускает «повышения».
- Импорты: битая строка CSV/XLSX/XML пишется в health detail, остальные строки файла читаются (status `partial`).
- Tender.Pro ключ можно переопределить через `TENDER_PRO_API_KEY` в `.env`; ЕИС запрашивает `recordsPerPage=_50` (нужна live-проверка при следующем запуске).
- Проверка: 149 pytest, офлайн smoke-run `--profile local` — карточка «г. Ялта» стала hot, битая строка дала partial.

Фаза 2 (следующая): дозагрузка detail-карточек B2B/Tender.Pro/Rostender, retry/backoff и троттлинг, open-data ЕИС, RTS-аккумулятор страниц, детект `suspect_empty` при дрейфе вёрстки.

## Обновление 2026-07-01

Работа остановлена на практическом RTS-сценарии: пользователь входит в RTS через свой Chrome-профиль, вручную ставит фильтр и открывает страницу выдачи, а парсер собирает текущие видимые строки из таблицы кабинета. Это важно, потому что RTS не дал API-доступ, а автоматическое листание в текущих сетевых условиях нестабильно.

Что уже сделано:

- `tender_parser/sources/rts_cabinet.py` доработан под jqGrid RTS-кабинета: видимые строки `tr.jqgrow` извлекаются как тендеры, включая номер закупки, заказчика, регион, предмет, цену, даты и статус.
- Исправлено определение состояния RTS-кабинета: залогиненная страница с текстом про вход в личный кабинет больше не помечается как login-page, если есть признак активной сессии.
- Товарные ключи расширены под реальные RTS-строки Крыма/Крымэнерго: `разъединитель`, `изолятор`, `трансформатор тока`, `измерительный трансформатор`, `трансформатор`, `ячейка`, `вакуумный выключатель`, `элегазовый выключатель`, `муфта`, `стойка УСО`, `УСО`.
- Матрица запросов теперь: 22 товарные группы x 5 регионов = 110 региональных запросов; B2B query-set = 22.
- Добавлены `Setup_Happ_RTS_Direct.bat` и `Setup_Happ_RTS_Direct.ps1` для попытки направить RTS/Chrome напрямую мимо VPN через Happ/sing-box.

Проверенный live-контекст:

- Chrome/CDP: локальный RTS Chrome-профиль, порт `127.0.0.1:9222`.
- RTS-страница: `223.rts-tender.ru/supplier/auction/Trade/Search.aspx`.
- Фильтр пользователя: `Респ. Крым`, 100 строк на странице.
- Pager RTS показывал примерно `1 - 100 из 9 900`.
- После расширения ключей по текущей странице: raw 100, unique 100, hot 8, review 8, excluded 84. `new_for_crm = 0`, потому что предыдущий запуск уже занес эти номера в локальный seen-state.

Важный вывод:

- Не продолжать упираться в полное автоматическое листание RTS до стабилизации сети/VPN. При кликах next/page-input RTS показывал модальное окно `Не удалось подключиться к серверу. Проверьте интернет-подключение и обновите страницу.`
- Следующая разработка: RTS Excel-first accumulator. Нужна команда/кнопка `добавить текущую страницу RTS`, которая берет видимые 100 строк из открытого Chrome, добавляет их в raw Excel/JSON, дедуплицирует по номеру закупки и не теряет широкий хвост. Потом отдельный офлайн-фильтр раскладывает накопленное в `горячие`, `на проверку`, `отсев`.

Дата: 2026-06-28

## Состояние

Реализована операционная версия парсера закупок: сбор, health-report источников, дедупликация, ежедневная CRM-очередь и подготовка запуска по расписанию.

- CLI: `python -m tender_parser run`
- Windows launcher: `Запустить_парсер.bat`
- Fast/local/RTS launchers: `run_tender_parser_silent.bat` defaults to `fast`, `Запустить_локальные_выгрузки.bat`, `Диагностика_RTS.bat`, `Открыть_RTS_кабинет_Chrome.bat`, `Собрать_RTS_кабинет.bat`
- Source parser: `tender_parser/sources/rts.py`
- RTS cabinet browser source: `tender_parser/sources/rts_cabinet.py`
- Browser helpers: `tender_parser/browser/session.py`, `tender_parser/browser/rts_cabinet.py`
- ETP GPB RSS parser: `tender_parser/sources/etp_gpb.py`
- Tender.Pro API parser: `tender_parser/sources/tender_pro.py`
- Torgi82 JSON parser: `tender_parser/sources/torgi82.py`
- EAT/Berezka integration API parser: `tender_parser/sources/eat.py`
- EIS/zakupki.gov.ru parser: `tender_parser/sources/eis.py`
- Rostender parser: `tender_parser/sources/rostender.py`
- Composite source: `tender_parser/sources/composite.py`
- Cabinet/export import source: `tender_parser/sources/imports.py`
- Document evidence analyzer: `tender_parser/documents.py`
- Tender enrichment layer: `tender_parser/enrichment.py`
- Source health types: `tender_parser/run_report.py`
- Cross-source deduplication: `tender_parser/dedup.py`
- Filters: `tender_parser/filters.py`
- Storage: `tender_parser/storage.py`
- Excel/JSON exports: `tender_parser/exporters/`
- Тесты: `tests/`
- Исследование ЭТП: `docs/etp_source_research_2026-05-29.md`
- Публичные endpoints настраиваются в `tender_parser/config.py` через `RTS_MARKET_ENDPOINTS`.
- Региональные endpoints могут задавать `region_hint`, чтобы закупки из региональной витрины не терялись из-за пустого региона в строке таблицы.
- Основной live-слой в обычном запуске - `EtpGpbRssSource`, `TenderProSource`, `Torgi82Source`, `B2BCenterSource`, `EatIntegrationSource`, `EisZakupkiSource`, `RostenderSource`, `RtsPublicSource`; RTS больше не резервный fallback и запускается в общем сборе.
- `CompositeSource.fetch_with_report` собирает `ok`/`empty`/`skipped`/`partial`/`blocked`/`timeout`/`ssl_error`/`error` для каждого источника и длительность запроса. Результат пишется в `exports/run_report.json`.
- При каждом обычном запуске CLI дополнительно читает `imports/` через `ImportFolderSource`; поддерживаются CSV/XLSX/XML с колонками названия, ссылки, номера, заказчика, региона, суммы, срока, даты публикации, источника и описания.
- Перед дедупликацией и фильтрацией CLI запускает `TenderEnricher(DocumentAnalyzer(base_dir / "documents"))`: TXT/CSV/XML/JSON/HTML/XLSX/DOCX/PDF-документы дают `detail_status`, `document_matches`, `delivery_region_evidence`, `source_confidence`.
- PDF читается по текстовому слою через `pypdf`; сканы без OCR не извлекаются. Папки `imports/` и `documents/` игнорируются Git.
- Высокоуверенные дубли ЕИС/Rostender склеиваются до фильтрации; приоритет у ЕИС. Перед хранением `TenderStorage` возвращает впервые увиденные карточки, из которых формируется `exports/new_tenders.json`.
- CLI создает `exports/latest.html` через `tender_parser/exporters/html_report.py`; это статический отчет для ручного просмотра actionable-тендеров и health-таблицы источников.
- CLI поддерживает `--profile full|fast|local|rts|rts-cabinet`: `full` - все источники, `fast` - без ЕИС/ГПБ/RTS timeout/captcha слоя, `local` - только `imports/` и `documents/`, `rts` - изолированная RTS-диагностика, `rts-cabinet` - текущая видимая выдача из авторизованного Chrome-профиля.
- `run_tender_parser_silent.bat` берет профиль из `TENDER_PARSER_PROFILE`, а если переменная не задана, запускает `fast`.
- CSV-шаблоны для ручных выгрузок: `docs/templates/import_template.csv` и `docs/templates/rts_export_template.csv`.
- Excel теперь начинается с листа `Новые`, затем идут `Горячие`, `На проверку`, `Широкий хвост`, `Отсеянные`. Для фонового запуска есть `run_tender_parser_silent.bat`; `Настроить_ежедневный_запуск.ps1 -Time "08:00" -Profile fast` создает задачу Windows Task Scheduler.
- `TenderRecord.match_confidence` разделяет карточки на `точное`, `вероятное` и `ручная проверка`; поле экспортируется в Excel, `latest.json` и `new_tenders.json`.
- Quality layer added: `review_priority` splits candidates into `hot`, `review`, `wide`, and `excluded`; поле хранится в SQLite и экспортируется в Excel/JSON для будущей CRM.
- Карточка без срока подачи больше не теряется автоматически: при подтвержденных товаре, регионе и сумме она попадает в `вероятное`.
- Добавлен `B2BCenterSource`, который читает публичную таблицу B2B-Center по 16 товарным запросам. В карточке списка обычно нет цены и региона, поэтому она попадает в `ручная проверка` до уточнения по первоисточнику.
- ЕИС, ЭТП ГПБ и Rostender используют общую матрицу из 80 запросов: 16 товарных групп x Симферополь, Севастополь, Крым, Запорожская и Херсонская области.
- 2026-06-21 задача `Tender Parser Daily` создана и проверена в Windows Task Scheduler: ежедневно в 08:00, launcher - `run_tender_parser_silent.bat`, состояние `Ready`.
- `EatIntegrationSource` активируется только при наличии `EAT_API_TOKEN` и `EAT_EXT_SYSTEM`. Без них источник отдает `SourceFetchError`, composite идет дальше.
- `.env` загружается CLI из `--base-dir` до построения источников; реальные секреты игнорируются Git, шаблон лежит в `.env.example`.
- `python -m tender_parser check-env` проверяет ЕАТ-настройки и не выводит значения токенов.
- `Настроить_EAT_env.ps1 -ApiToken "..." -ExtSystem "..."` записывает локальный `.env`, не печатает токен и сразу запускает `check-env`; инструкция для пользователя - `docs/EAT_TOKEN_SETUP.md`.
- RTS-Tender foundation: `docs/rts_tender_foundation_2026-06-28.md`. Публичный RTS v2 уже пишет health-report по каждому endpoint, использует focused `RTS_SEARCH_QUERIES` и `RTS_TIMEOUT_SECONDS=8`. Кабинетный режим добавлен как `RtsCabinetBrowserSource`: пользователь вручную входит через `Открыть_RTS_кабинет_Chrome.bat`, затем запускает `Собрать_RTS_кабинет.bat`.
- ЕИС/`zakupki.gov.ru` добавлен как главный широкий источник для 44-ФЗ/223-ФЗ. Отдельные ЭТП остаются дополнительными каналами для коммерческих, малых и региональных закупок.
- `EisZakupkiSource` отключает `session.trust_env`, потому что системный proxy в текущей среде приводил к долгим таймаутам на `zakupki.gov.ru`.

## Последняя проверка

Команды, которые использовались для проверки:

```powershell
python -m pytest -v
python -m tender_parser run
```

На live-проверке 2026-05-29 Rosatom публичный источник вернул captcha/rate-limit, региональные RTS endpoints таймаутились, общий RTS падал по SSL. Поэтому добавлен Rostender fallback. ЭТП ГПБ RSS также таймаутится из текущей сети, но источник подключен с коротким timeout и не блокирует дальнейший сбор. Tender.Pro API отвечает стабильно и добавляет открытые процедуры в общий поток. Торги82 подключен через `https://etp.torgi82.ru/searchServlet`; endpoint отдает JSON последних 20 процедур.

Live-run 2026-06-28 после включения RTS в основной слой показал, что RTS реально добавляет кандидатов: 2 новых `rts-rosatom` записи попали в `new_tenders.json` на ручную проверку. После tuning `RTS_TIMEOUT_SECONDS=8` worktree live-run занял 206 секунд, RTS timeout-строки стали по ~8 секунд; `new_count` worktree-запуска не сравнивать с основной базой, потому что локальная SQLite в worktree была свежая.

Последний live-запуск 2026-06-28 после слоя приоритизации:

- `Найдено`: 1633
- `После дедупликации`: 1614
- `Горячие`: 23
- `На проверку`: 15
- `Широкий хвост`: 139
- `Отсеянные`: 1437
- `Новые для CRM`: 177
- `latest.json`: 177 actionable, из них 37 Rostender, 138 B2B-Center, 1 Торги82, 1 Tender.Pro
- `review_priority`: 23 `hot`, 15 `review`, 139 `wide`
- `run_report.json`: ЭТП ГПБ - timeout, ЕАТ - пропущен без токена, ЕИС - timeout, Tender.Pro/Торги82/B2B-Center/Rostender - OK.

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

Следующий практический уровень охвата: **ЕАТ по токену, RTS-Tender кабинет/API, затем альтернативный официальный канал ЕИС и углубление B2B-Center**.

1. Получить в ЛК ЕАТ токен и код внешней системы, затем выполнить `Настроить_EAT_env.ps1 -ApiToken "..." -ExtSystem "..."`.
2. Проверить `EatIntegrationSource` live-запуском через `python -m tender_parser run --profile fast`.
3. Проверить RTS cabinet live: открыть `Открыть_RTS_кабинет_Chrome.bat`, войти вручную, открыть выдачу закупок, запустить `Собрать_RTS_кабинет.bat`, затем уточнить selectors по `run_report.json`.
4. Проверить альтернативный официальный канал ЕИС: XML/open-data или кабинетный экспорт, потому что HTML search может таймаутиться. Аналогично проверить другой endpoint ЭТП ГПБ.
5. Углубить B2B-Center через личный кабинет/API или подробную карточку, чтобы вытаскивать регион и цену.
6. Углубить Торги82: найти корректный GWT/searchServlet payload для ключевых слов и пагинации, потому что простой endpoint сейчас дает только последние 20 процедур.
7. После этого смотреть Фабрикант, ОТС и OnlineContract.
8. Для CRM читать `exports/new_tenders.json` как дельту, `exports/latest.json` как полную очередь и `exports/run_report.json` как диагностику.
9. При изменении словарей править `tender_parser/config.py` и добавлять focused tests в `tests/test_filters.py`.
10. Для быстрого прироста охвата без нового scraper брать выгрузки из кабинетов/площадок, класть их в `imports/`, а технические задания/карточки в текстовом виде - в `documents/`, затем запускать обычный `python -m tender_parser run`.
11. Для ежедневной ручной проверки сначала смотреть `exports/latest.html`; для быстрой обработки кабинетных файлов запускать `python -m tender_parser run --profile local`, для публичного RTS-разбора - `python -m tender_parser run --profile rts`, для открытой RTS-кабинетной выдачи - `python -m tender_parser run --profile rts-cabinet`.

## Git

Рабочая ветка: `codex/rts-tender-parser`.

Перед передачей дальше проверить:

```powershell
git status --short
python -m pytest -v
```
