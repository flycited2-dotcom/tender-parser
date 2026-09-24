# База поставщиков

Модуль индексирует входящие и исходящие письма Gmail и показывает накопленные
сведения в отдельной Google Spreadsheet. Он **не отправляет письма, не создаёт
черновики и не меняет Gmail-ярлыки**. История цен и писем хранится по Gmail
`message_id`/`thread_id`; техническое состояние и источники фактов — в SQLite.

## Где находится код

| Часть | Путь |
| --- | --- |
| Gmail OAuth, backfill, History API | `tender_parser/supplier_intelligence/gmail_collector.py`, `sync.py` |
| Разбор письма и вложений | `message_parser.py`, `extraction.py`, `signature_parser.py`, `attachment_parser.py` |
| Дедупликация, контакты, товары, КП | `resolver.py`, `storage.py`, `product_classifier.py` |
| Google Sheets | `sheets_store.py`, `sheet_schema.py` |
| Поиск поставщиков | `search.py` |
| Настройки категорий и алиасов | `config/product_categories.yaml`, `config/supplier_aliases.yaml` |

Существующий CSV-реестр `tender_parser/supplier_registry.py`, тендерная Google
Sheet и импорт прайсов `content-factory` не перезаписываются. Связь с RFQ
строится по Gmail `thread_id` и ID, извлечённым из темы/тела письма. В кодовой
базе нет отдельного готового RFQ-регистра с `TENDER_ID/RFQ_ID`; если он находится
в другой таблице, его можно добавить как дополнительный источник контекста.

## Подготовка

1. Установить зависимости: `python -m pip install -r requirements.txt`.
2. Создать `.env` по `.env.example` в корне проекта. Таблица поставщиков
   использует отдельный `SUPPLIER_SPREADSHEET_ID`, чтобы не затронуть тендерный
   реестр. `GOOGLE_SERVICE_ACCOUNT_FILE` указывает на JSON сервисного аккаунта
   Google; дайте его `client_email` право редактирования таблицы.
3. Создать в Google Cloud OAuth client типа **Desktop app**, включить Gmail API.
   Записать путь JSON в `GMAIL_OAUTH_CLIENT_FILE`.
4. Каждый Gmail-ящик подключается отдельно. Войти именно в указанный аккаунт:

   ```powershell
   python -m tender_parser.supplier_intelligence auth --account termoark@gmail.com
   python -m tender_parser.supplier_intelligence auth --account flycited@gmail.com
   ```

   Токены сохраняются в `GMAIL_TOKEN_1`, `GMAIL_TOKEN_2` и должны оставаться
   вне Git. Используется единственный OAuth scope `gmail.readonly`.

## Первый импорт и ежедневная работа

```powershell
python -m tender_parser.supplier_intelligence backfill
python -m tender_parser.supplier_intelligence sync --dry-run
python -m tender_parser.supplier_intelligence sync
python -m tender_parser.supplier_intelligence stats
python -m tender_parser.supplier_intelligence find "сервировочные тележки"
python -m tender_parser.supplier_intelligence find "POZIS"
python -m tender_parser.supplier_intelligence suggest --product-name "ХФ-140-2" --brand POZIS
python -m tender_parser.supplier_intelligence reprocess --message-id GMAIL_MESSAGE_ID
python -m tender_parser.supplier_intelligence rebuild-supplier SUP-000001
```

Можно также выполнить `cd supplier_intelligence; python main.py sync`.
`sync` сам запускает backfill для ящика без сохранённого курсора. Повторный
запуск пропускает уже обработанные письма; `--dry-run` использует временную
копию SQLite и не пишет в основную БД или Google Sheets.

Backfill перечисляет все обычные письма Gmail, включая входящие, исходящие и
архив. Пагинация и исходный `history_id` сохраняются в SQLite. После полного
прохода модуль догоняет письма, поступившие во время импорта. В дальнейшем
`sync` читает только Gmail History API; при истёкшем курсоре сверяет все ID
с локальным реестром. Ошибка одного письма попадает в `errors`, после трёх
попыток цикл продолжает работу, следующий запуск повторяет проблемное письмо.

Google Sheets обновляется пакетами только по изменившимся строкам. Листы:
`SUPPLIERS`, `CONTACTS`, `SUPPLIER_CATEGORIES`, `CATEGORY_FILTER`, `SUPPLIER_PRODUCTS`, `QUOTES`,
`INTERACTIONS`, `ALIASES`, `REVIEW_QUEUE`, `PROCESSING_LOG`, `ATTACHMENTS`,
`DASHBOARD`. Полный текст письма в Sheet не записывается.

На `CATEGORY_FILTER` находится по одной строке на сочетание поставщика и
товарной категории: группа фильтра, четыре уровня, компания, контакты и
идентификатор исходного письма. Созданы сохранённые фильтры для всех групп словаря,
включая медоборудование, строительство, климат, электронику и медпрепараты.
Обычный фильтр заголовка позволяет также выбирать конкретные подкатегории.
Email в основных листах открывается как ссылка `mailto:`.

## Автоматический запуск

На Windows после OAuth-авторизации запустите из корня проекта:

```powershell
powershell -ExecutionPolicy Bypass -File .\supplier_intelligence\Install-ScheduledSync.ps1
```

Задача Windows запускает `sync` раз в 15 минут. Пока идёт историческая загрузка,
каждый запуск берёт по одной странице писем из каждого ящика; после её завершения
используется инкрементальное обновление Gmail History. Результат каждого запуска и
код завершения записываются в `logs/supplier_intelligence/runs.log`, вывод и
ошибки — в отдельные файлы `sync-*.stdout.log` и `sync-*.stderr.log`.
Компьютер должен быть включён; по умолчанию задача работает под вошедшим пользователем. На VPS доступны
`deploy/supplier-intelligence.service` и `.timer`; перед установкой измените
`WorkingDirectory`, путь к Python и разместите защищённые OAuth-файлы на VPS.

## Настройки

| Переменная | Назначение |
| --- | --- |
| `GMAIL_ACCOUNT_n` / `GMAIL_TOKEN_n` | Адрес ящика и путь его отдельного OAuth token JSON |
| `GMAIL_OAUTH_CLIENT_FILE` | OAuth client JSON типа Desktop |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | JSON сервисного аккаунта, уже используемого Google Sheets-кодом проекта |
| `SUPPLIER_SPREADSHEET_ID` | ID отдельной таблицы «База поставщиков» |
| `SUPPLIER_DB_PATH` | Путь SQLite, по умолчанию `data/supplier_intelligence.db` |
| `SUPPLIER_CONFIDENCE_THRESHOLD` | Порог автосвязи компаний, по умолчанию `0.70` |
| `SUPPLIER_BATCH_SIZE` | Размер страницы backfill, до 500 для Gmail API |
| `SUPPLIER_MAX_RETRIES` | Попытки обработки одного письма, по умолчанию 3 |
| `SUPPLIER_ATTACHMENT_MAX_BYTES` | Максимальный размер вложения для чтения текста |
| `SUPPLIER_OWN_EMAILS` / `SUPPLIER_OWN_DOMAINS` | Дополнительные собственные адреса и домены через запятую |
| `SUPPLIER_IGNORED_SENDER_DOMAINS` / `SUPPLIER_IGNORED_SENDER_PREFIXES` | Дополнительные системные домены и префиксы автоматических адресов |

Словарь категорий можно расширять в `product_categories.yaml`: сейчас это 28
закупочных групп и более 400 формулировок/синонимов. Новые группы автоматически
получают сохранённый фильтр при следующей синхронизации. Категория добавляется
только при совпадении с названием товара в свежем тексте письма, теме или имени
вложения; неоднозначные связи поставщиков попадают в `REVIEW_QUEUE`.
Цена без чёткой связи с товарной строкой не записывается как достоверная цена.

## Проверка

```powershell
python -m pytest tests/test_supplier_intelligence_*.py -q
```

После успешного backfill проверьте `stats`, лист `DASHBOARD`, несколько
цепочек `INTERACTIONS` и соответствующие `QUOTES`. Для каждой записи сохраняется
исходный mailbox/message/thread ID. `SUPPLIER_ID` остаётся неизменным при
дальнейшем обновлении.
