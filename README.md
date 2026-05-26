# Парсер закупок RTS-Tender

Парсер ищет публичные актуальные закупки RTS-Tender по заданным категориям, фильтрует их по регионам, сумме от 30 000 рублей и стоп-темам, сохраняет историю в SQLite и выгружает Excel.

## Запуск

Двойной клик по файлу:

```text
Запустить_парсер.bat
```

После завершения откроется папка `exports`.

## Результаты

- `data/tenders.db` - локальная история закупок.
- `exports/tenders_YYYY-MM-DD.xlsx` - Excel с листами `Подходящие` и `Отсеянные`.
- `exports/latest.json` - JSON для будущей CRM.

## Ручной запуск

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m tender_parser run
```

## Ограничения первой версии

- Работает только с публичными страницами без авторизации.
- Не подает заявки автоматически.
- Не обходит капчу и закрытые разделы.
- Первый источник - публичный раздел `www.rosatom.rts-tender.ru/market/`.
