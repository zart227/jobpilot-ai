"""Parse Weblancer listing HTML without a browser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

_CF_MARKERS = (
    "just a moment",
    "checking your browser",
    "один момент",
    "проверяем, человек ли вы",
    "cf-challenge",
    "challenges.cloudflare.com",
)

_MODERN_ROW = re.compile(
    r'<div\s+class="row"[^>]*>(.*?)</div>\s*(?=<div\s+class="row"|</div>)',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_LINK = re.compile(
    r'<h2[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_AMOUNT = re.compile(
    r'<div[^>]*class="[^"]*amount[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_DESC = re.compile(
    r'<p[^>]*class="[^"]*text-5[^"]*"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_LEGACY_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_NEXT_LINK = re.compile(
    r'<a[^>]+href="(/freelance/[^"]+-\d+/)\"[^>]*>([^<]{2,300})</a>',
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class WeblancerListingRow:
    title: str
    href: str
    description: str
    budget_text: str


def _strip_tags(text: str) -> str:
    return unescape(_TAG.sub(" ", text)).replace("\xa0", " ").strip()


def is_cloudflare_challenge_html(html: str) -> bool:
    lower = html[:12_000].lower()
    return any(marker in lower for marker in _CF_MARKERS)


def parse_listing_html(html: str) -> list[WeblancerListingRow]:
    if not html or is_cloudflare_challenge_html(html):
        return []

    rows: list[WeblancerListingRow] = []
    if "cols_table" in html:
        for block in _MODERN_ROW.findall(html):
            title_match = _TITLE_LINK.search(block)
            if not title_match:
                continue
            href = title_match.group(1).strip()
            title = _strip_tags(title_match.group(2))
            if not title:
                continue
            amount_match = _AMOUNT.search(block)
            budget_text = _strip_tags(amount_match.group(1)) if amount_match else ""
            desc_match = _DESC.search(block)
            description = _strip_tags(desc_match.group(1)) if desc_match else title
            rows.append(
                WeblancerListingRow(
                    title=title,
                    href=href,
                    description=description,
                    budget_text=budget_text,
                )
            )
        if rows:
            return rows

    for block in _LEGACY_TR.findall(html):
        link = re.search(r'<td[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
        if not link:
            continue
        title = _strip_tags(link.group(2))
        if not title:
            continue
        href = link.group(1).strip()
        tds = re.findall(r"<td[^>]*>(.*?)</td>", block, re.I | re.S)
        budget_text = _strip_tags(tds[1]) if len(tds) > 1 else ""
        rows.append(
            WeblancerListingRow(
                title=title,
                href=href,
                description=title,
                budget_text=budget_text,
            )
        )
    if rows:
        return rows

    seen_href: set[str] = set()
    for href, title_raw in _NEXT_LINK.findall(html):
        title = _strip_tags(title_raw)
        if not title or href in seen_href:
            continue
        if "/freelance/" not in href:
            continue
        seen_href.add(href)
        rows.append(
            WeblancerListingRow(
                title=title,
                href=href,
                description=title,
                budget_text="",
            )
        )
    return rows
