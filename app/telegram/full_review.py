"""Display exact outbound text before exposing approval controls."""

from __future__ import annotations

import hashlib

REVIEW_FORMAT = "full_text_v1"


def split_review_text(content: str, limit: int = 3000) -> list[str]:
    """Telegram counts UTF-16 units; do not split astral characters or alter text."""
    if limit < 2:
        raise ValueError("Review chunk limit must be >=2")
    chunks, current, units = [], [], 0
    for character in content:
        width = 2 if ord(character) > 0xFFFF else 1
        if units + width > limit:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(character)
        units += width
    if current:
        chunks.append("".join(current))
    return chunks


async def send_full_review(send, *, content: str, reference: str, heading: str, markup):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("No outbound text to review")
    chunks = split_review_text(content)
    digest = hashlib.sha256(content.encode()).hexdigest()
    # Plain text avoids HTML/Markdown normalization, broken escaping and truncation.
    for header_chunk in split_review_text(heading):
        await send(header_chunk, parse_mode=None, reply_markup=None)
    for number, chunk in enumerate(chunks, 1):
        await send(
            f"Точный текст • {reference} • часть {number}/{len(chunks)}\n\n{chunk}",
            parse_mode=None,
            reply_markup=None,
        )
    return await send(
        f"Подтверждение • {reference}\n"
        f"Показан весь текст: {len(content)} символов, частей: {len(chunks)}.\n"
        f"SHA-256: {digest}\n"
        "Одобрение относится к тексту и параметрам выше.",
        parse_mode=None,
        reply_markup=markup,
    )
