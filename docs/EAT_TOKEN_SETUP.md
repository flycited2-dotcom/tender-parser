# ЕАТ / Березка: подключение токена

## Что нужно получить в личном кабинете

В личном кабинете ЕАТ/Березка нужен доступ для внешней информационной системы:

- `EAT_API_TOKEN` - API-токен;
- `EAT_EXT_SYSTEM` - код внешней системы;
- при нестандартной авторизации - название заголовка и схема авторизации.

Секреты не хранятся в Git. Они должны лежать только в локальном файле `.env`, который уже добавлен в `.gitignore`.

## Быстрая настройка

Из папки проекта:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Настроить_EAT_env.ps1 -ApiToken "ТОКЕН_ИЗ_ЛК" -ExtSystem "КОД_СИСТЕМЫ"
```

Скрипт создаст `.env` и сразу выполнит:

```powershell
python -m tender_parser check-env
```

Ожидаемый результат:

```text
EAT_API_TOKEN: configured
EAT_EXT_SYSTEM: configured
```

## Если в документации ЕАТ указан другой заголовок

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Настроить_EAT_env.ps1 `
  -ApiToken "ТОКЕН_ИЗ_ЛК" `
  -ExtSystem "КОД_СИСТЕМЫ" `
  -AuthHeader "Authorization" `
  -AuthScheme "Bearer" `
  -MaxDetails 100
```

## Проверка после настройки

```powershell
python -m tender_parser run --profile fast
```

В `exports/run_report.json` источник `EatIntegrationSource` должен перейти из `skipped` в `ok`, `empty`, `partial` или `error` с конкретной диагностикой от API.
