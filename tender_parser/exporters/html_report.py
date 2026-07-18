from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from tender_parser.models import TenderRecord
from tender_parser.run_report import SourceFetchResult


PRIORITY_LABELS = {
    "hot": "Горячие",
    "review": "На проверку",
    "wide": "Широкий хвост",
}


def _safe_url(url: str) -> str:
    # Только http/https: javascript:-схемы из недоверенной вёрстки не должны стать кликабельными.
    return url if url.startswith(("http://", "https://")) else "#"


def export_html_report(
    tenders: list[TenderRecord],
    output_path: Path,
    *,
    source_report: SourceFetchResult,
    raw_count: int,
    unique_count: int,
    new_count: int,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(tenders, source_report, raw_count, unique_count, new_count),
        encoding="utf-8",
    )
    return output_path


def _render_html(
    tenders: list[TenderRecord],
    source_report: SourceFetchResult,
    raw_count: int,
    unique_count: int,
    new_count: int,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hot = _count_priority(tenders, "hot")
    review = _count_priority(tenders, "review")
    wide = _count_priority(tenders, "wide")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Отчет по тендерам</title>
  <style>
    body {{ margin: 0; font: 14px/1.45 Arial, sans-serif; color: #1d2327; background: #f6f7f8; }}
    header {{ padding: 22px 28px; background: #ffffff; border-bottom: 1px solid #d8dde3; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    main {{ padding: 22px 28px 36px; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 18px; }}
    .stat {{ background: #ffffff; border: 1px solid #d8dde3; border-radius: 8px; padding: 12px; }}
    .stat b {{ display: block; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; background: #ffffff; border: 1px solid #d8dde3; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e5e8eb; vertical-align: top; text-align: left; }}
    th {{ background: #eef1f4; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    a {{ color: #0b63ce; }}
    .badge {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #eef1f4; font-size: 12px; }}
    .hot {{ background: #ffe1d6; }}
    .review {{ background: #fff1bd; }}
    .wide {{ background: #dcecff; }}
    .muted {{ color: #6b7280; }}
    .sources {{ margin-top: 22px; }}
  </style>
</head>
<body>
<header>
  <h1>Отчет по тендерам</h1>
  <div class="muted">Сформировано: {escape(generated_at)}</div>
</header>
<main>
  <section class="stats">
    {_stat("Всего найдено", raw_count)}
    {_stat("После дедупликации", unique_count)}
    {_stat("Новые для CRM", new_count)}
    {_stat("Горячие", hot)}
    {_stat("На проверку", review)}
    {_stat("Широкий хвост", wide)}
  </section>
  <table>
    <thead>
      <tr>
        <th>Приоритет</th>
        <th>Тендер</th>
        <th>Регион / сумма / срок</th>
        <th>Источник</th>
        <th>Доказательства</th>
      </tr>
    </thead>
    <tbody>
      {''.join(_tender_row(tender) for tender in tenders) or _empty_row()}
    </tbody>
  </table>
  <section class="sources">
    <h2>Источники</h2>
    <table>
      <thead><tr><th>Источник</th><th>Статус</th><th>Найдено</th><th>Время</th><th>Детали</th></tr></thead>
      <tbody>{''.join(_source_row(item) for item in source_report.health) or _empty_source_row()}</tbody>
    </table>
  </section>
</main>
</body>
</html>
"""


def _stat(label: str, value: int) -> str:
    return f'<div class="stat"><b>{value}</b><span>{escape(label)}</span></div>'


def _count_priority(tenders: list[TenderRecord], priority: str) -> int:
    return sum(1 for tender in tenders if tender.review_priority == priority)


def _tender_row(tender: TenderRecord) -> str:
    priority = tender.review_priority or ""
    css_class = priority if priority in PRIORITY_LABELS else ""
    evidence = "<br>".join(
        value
        for value in [
            _line("Совпадения", ", ".join(tender.document_matches)),
            _line("Evidence", tender.delivery_region_evidence),
            _line("Причина", tender.include_reason or tender.exclude_reason),
        ]
        if value
    )
    terms = f'<div class="muted">{escape(", ".join(tender.matched_terms))}</div>' if tender.matched_terms else ""
    return f"""<tr>
  <td><span class="badge {css_class}">{escape(PRIORITY_LABELS.get(priority, priority))}</span></td>
  <td><a href="{escape(_safe_url(tender.url))}" target="_blank" rel="noopener">{escape(tender.title)}</a>{terms}<div class="muted">{escape(tender.customer or "")}</div></td>
  <td>{escape(tender.region or "")}<br>{escape(_money(tender.price))}<br>{escape(_date(tender.deadline))}</td>
  <td>{escape(tender.source)}<br><span class="muted">{escape(tender.tender_number or "")}</span><br><span class="muted">confidence {tender.source_confidence:.2f}</span></td>
  <td>{evidence}</td>
</tr>"""


def _source_row(item: object) -> str:
    return f"""<tr>
  <td>{escape(item.source)}</td>
  <td>{escape(item.status)}</td>
  <td>{item.found}</td>
  <td>{item.elapsed_seconds:.1f} сек.</td>
  <td>{escape(item.detail)}</td>
</tr>"""


def _line(label: str, value: str) -> str:
    return f"<b>{escape(label)}:</b> {escape(value)}" if value else ""


def _money(value: float | None) -> str:
    return f"{value:,.2f} руб.".replace(",", " ") if value is not None else "сумма не указана"


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "срок не указан"


def _empty_row() -> str:
    return '<tr><td colspan="5" class="muted">Нет тендеров для ручной проверки.</td></tr>'


def _empty_source_row() -> str:
    return '<tr><td colspan="5" class="muted">Нет данных по источникам.</td></tr>'
