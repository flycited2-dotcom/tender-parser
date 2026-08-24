# Личный Telegram-агент по оборудованию

Агент принимает текст, голосовые сообщения и документы с ТЗ. Мозг агента — одна постоянная сессия Codex, авторизованная через подписку ChatGPT; отдельный OpenAI API-ключ не нужен. Codex обращается только на чтение к MCP-серверу `tender-knowledge`, который предоставляет:

- основному каталогу I-T-P;
- климатическому хабу поставщиков, если запрос относится к климатической технике;
- локальной базе `data/tenders.db`;
- базе знаний `knowledge_base/`.

Каталог I-T-P синхронизируется проектом
[`ClaudeDesign_tehnika_site/web-store`](https://github.com/flycited2-dotcom/ClaudeDesign_tehnika_site/tree/codex/b2b-telegram-assistant/web-store).
Production-мост `https://climat-simf.ru/api/internal/tender-products` читает локальную
PostgreSQL-базу магазина и не создаёт заказов у поставщика.

## 1. Создание бота

1. В Telegram откройте `@BotFather`, выполните `/newbot` и сохраните токен.
2. Узнайте свой числовой `user_id` через `@userinfobot` или команду `/whoami` уже запущенного агента.
3. Добавьте в `.env` настройки из `.env.example`.

Минимально нужны `TELEGRAM_AGENT_BOT_TOKEN`,
`TELEGRAM_AGENT_ALLOWED_USER_IDS`, `TENDER_SUPPLIER_API_URL` и
`TENDER_SUPPLIER_API_TOKEN`. Переменная `TELEGRAM_BOT_TOKEN` относится к другому
боту — ежедневным отчётам и командам парсера.

Один раз проверьте авторизацию Codex:

```powershell
codex -c service_tier='fast' login status
```

Должно появиться `Logged in using ChatGPT`. `OPENAI_API_KEY` агент не использует.

## 2. Установка и запуск

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m tender_parser.telegram_agent
```

Или дважды щёлкните `Запустить_личного_Telegram_агента.bat`.

Бот использует long polling, поэтому публичный URL и webhook не нужны. Первый запрос создаёт отдельную сессию Codex, её идентификатор сохраняется в `data/telegram_codex_session.json`; все следующие сообщения продолжают именно её. `/reset` создаёт новый чистый чат. Для постоянной работы транспорт Telegram должен оставаться запущенным на ПК или сервере.

Голос распознаётся локально через `faster-whisper`, без OpenAI API. При первом голосовом сообщении модель `small` загружается и кешируется на компьютере.

## 3. Примеры

- `Найди цветное лазерное МФУ А4, двусторонняя печать, Wi-Fi, до 45 тысяч.`
- `Нужна сплит-система на 35 м², инвертор, обогрев до -15 °C.`
- `Покажи тендеры на принтеры в Крыму.`
- Голосовое сообщение с любой из этих команд.
- PDF/DOCX/XLSX с ТЗ и подписью `Подбери оборудование по этому документу`.

Команды: `/status`, `/reset`, `/whoami`, `/help`, `/agent_on`, `/agent_off`.

Кнопки «▶️ Запустить агента» и «⏸ Выключить агента» управляют обработкой заданий.
При выключении Telegram-транспорт остаётся на связи, поэтому кнопку запуска можно нажать
в любой момент. Состояние сохраняется в `data/telegram_agent_state.json` и переживает
перезапуск компьютера. Полный запуск транспортного процесса после входа в Windows выполняет
задача планировщика `Tender Personal Telegram Agent`.

## Безопасность

Доступ разрешён только идентификаторам из
`TELEGRAM_AGENT_ALLOWED_USER_IDS`. Токены находятся в `.env`, который исключён
из Git. Codex запускается в режиме `read-only` с запретом подтверждений. Агент не
умеет резервировать и заказывать товар, подавать заявки или отправлять сообщения
поставщикам.
