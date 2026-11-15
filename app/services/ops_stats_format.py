"""Operator-facing text for /stats (Telegram) and related summaries."""

from __future__ import annotations

import html
from typing import Any

from app.utils.delivery_errors import humanize_kwork_browser_error


def _preview_summary(last_error: str | None) -> str:
    if not last_error:
        return "—"
    human = humanize_kwork_browser_error(last_error)
    text = human or last_error
    return text.replace("\n", " ")[:220]


def format_ops_stats_telegram(ops: dict[str, Any]) -> str:
    sa = ops.get("send_attempts") or {}
    outbox = ops.get("notification_outbox") or {}
    jobs = ops.get("jobs") or {}
    last = ops.get("last_success") or {}
    previews = sa.get("unknown_result_previews") or []

    lines = [
        "<b>📊 JobPilot /stats</b>",
        f"\n<b>Отправки:</b> unknown_result={sa.get('unknown_result', 0)}",
        f" • known_failed={sa.get('known_failed', 0)} • sending={sa.get('sending', 0)}",
        f"\n<b>Outbox:</b> pending={outbox.get('pending_or_claimed', 0)}",
        f" • needs_attention={outbox.get('needs_attention', 0)}",
        f"\n<b>Jobs:</b> retryable_error={jobs.get('retryable_error', 0)}",
        f" • permanent_error={jobs.get('permanent_error', 0)}",
    ]
    ext = last.get("confirmed_external_send_at")
    if ext:
        lines.append(f"\n<b>Последняя live-отправка:</b> {html.escape(ext)}")

    if previews:
        lines.append("\n\n<b>Неопределённые попытки (unknown_result):</b>")
        for index, row in enumerate(previews, 1):
            order = row.get("order_id") or "?"
            title = row.get("title") or "без названия"
            label = row.get("error_label") or "—"
            summary = html.escape(_preview_summary(row.get("last_error")))
            lines.append(
                f"\n{index}. №{html.escape(str(order))} — {html.escape(str(title)[:60])}"
                f"\n   <i>{summary}</i> ({html.escape(str(label))})"
            )
    elif int(sa.get("unknown_result") or 0) == 0:
        lines.append("\n\n✅ Нет попыток в статусе unknown_result.")
    else:
        lines.append("\n\n⚠️ Есть unknown_result, но список превью пуст (проверьте API /stats).")

    text = "".join(lines)
    if len(text) > 4096:
        return text[:4093] + "..."
    return text
