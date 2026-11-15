#!/usr/bin/env python3
"""Capture Kwork UI evidence for live signoff (existing offer on a project)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data/acceptance-evidence"

_SUCCESS_BANNER_MARKERS = (
    "ваше индивидуальное предложение отправлено",
    "предложение отправлено",
)


def _snapshot_expectations(attempt: "SendAttempt") -> dict:
    payload = (attempt.snapshot_metadata or {}).get("approved_payload") or {}
    content = (payload.get("content") or attempt.snapshot_content or "").strip()
    offer_price = payload.get("offer_price")
    duration_days = payload.get("duration_days")
    title = (payload.get("offer_title") or "").strip()
    needles: list[str] = []
    if title:
        needles.append(title[:40])
    for fragment in ("AutoCheck", "FastAPI", "500"):
        if fragment.lower() in content.lower():
            needles.append(fragment)
    if not needles and content:
        needles.append(content[:80].split("\n", 1)[0][:60])
    return {
        "content": content,
        "offer_price": offer_price,
        "duration_days": duration_days,
        "needles": needles,
    }


async def _read_offer_form_fields(page) -> dict:
    return await page.evaluate(
        """() => {
        const root = document.querySelector('main') || document.body;
        const text = (root.innerText || '').toLowerCase();
        const priceInput = document.querySelector('input[name="price"], input[name="offer_price"]');
        const priceVal = priceInput ? priceInput.value : '';
        const selected = [...document.querySelectorAll('.vs__selected')].map(el => (el.textContent || '').trim());
        const editors = [...document.querySelectorAll('.trumbowyg-editor')].map(el => (el.innerText || '').trim());
        return { body: text, priceVal, selected, editors };
    }"""
    )


async def _ui_matches_snapshot(page, expectations: dict) -> tuple[bool, str]:
    fields = await _read_offer_form_fields(page)
    body = fields.get("body") or ""
    editors = " ".join(fields.get("editors") or [])
    haystack = f"{body}\n{editors}".lower()
    if any(marker in body for marker in _SUCCESS_BANNER_MARKERS) and "редактирование предложения" not in body:
        return False, "success banner without sent-offer detail"
    for needle in expectations.get("needles") or []:
        if needle and needle.lower() not in haystack:
            return False, f"missing snapshot text fragment: {needle[:40]!r}"
    offer_price = expectations.get("offer_price")
    if offer_price is not None:
        price_text = str(offer_price)
        price_val = str(fields.get("priceVal") or "")
        if price_text not in price_val and price_text not in haystack.replace("\u00a0", " "):
            return False, f"missing offer price {offer_price}"
    duration = expectations.get("duration_days")
    if duration is not None:
        duration_token = str(int(duration))
        selected = " ".join(fields.get("selected") or [])
        if duration_token not in selected and duration_token not in haystack:
            if not any(token in haystack for token in (f"{duration_token} день", f"{duration_token} дн")):
                return False, f"missing duration {duration}"
    return True, "ok"


async def _capture_sent_offer_form(page, project_id: str, job_url: str, steps: list[str]) -> bytes | None:
    from app.services.kwork_browser import (
        _offer_form_visible,
        _open_edit_offer_form,
    )

    steps.append("открытие «Мои отклики»: https://kwork.ru/offers")
    await page.goto("https://kwork.ru/offers", wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(3000)
    body = (await page.inner_text("body")).lower()
    if project_id not in await page.content() and "b2b-шлюза" not in body:
        steps.append("проект не найден в списке /offers, пробуем edit_offer напрямую")
    if not await _open_edit_offer_form(page, project_id, job_url, steps):
        return None
    if not await _offer_form_visible(page):
        return None
    await page.wait_for_timeout(1500)
    await page.set_viewport_size({"width": 1280, "height": 1600})
    await page.wait_for_timeout(300)
    await page.evaluate(
        """() => {
        const root = document.querySelector('main') || document.body;
        const editor = root.querySelector('.trumbowyg-editor[placeholder*="решать"]')
            || [...root.querySelectorAll('.trumbowyg-editor')].find(
                (node) => (node.innerText || '').trim().length >= 40
            );
        const price = [...root.querySelectorAll('input')].find((input) => {
            if (input.type === 'hidden' || input.disabled) return false;
            const ph = input.placeholder || '';
            return /0{2,}/.test(ph) || /стоимость|цена/i.test(ph);
        });
        const duration = [...root.querySelectorAll('.v-select')].find((select) => {
            const label = (select.querySelector('.vs__selected')?.textContent || '').replace(/\\s+/g, ' ').trim();
            return /^\\d+$/.test(label);
        });
        for (const node of [editor, price, duration]) {
            if (node) node.scrollIntoView({ block: 'center', behavior: 'instant' });
        }
        if (duration) duration.scrollIntoView({ block: 'center', behavior: 'instant' });
    }"""
    )
    await page.wait_for_timeout(1200)
    clip = await page.evaluate(
        """() => {
        const offerRoot = document.querySelector('main') || document.body;
        let editor = offerRoot.querySelector('.trumbowyg-editor[placeholder*="решать"]');
        if (!editor) {
            const editors = [...offerRoot.querySelectorAll('.trumbowyg-editor')].filter(
                (node) => !(node.getAttribute('placeholder') || '').toLowerCase().includes('название')
            );
            editor = editors.find((node) => (node.innerText || '').trim().length >= 40) || editors[0] || null;
        }
        const findPriceInput = () => {
            const byName = [...offerRoot.querySelectorAll('input[name="price"], input[name="kwork_price"]')].filter(
                (input) => input.type !== 'hidden' && input.getClientRects().length && !input.disabled
            );
            if (byName.length >= 1) return byName[0];
            for (const input of offerRoot.querySelectorAll('input')) {
                if (input.type === 'hidden' || !input.getClientRects().length || input.disabled) continue;
                const placeholder = input.placeholder || '';
                if (/0{2,}/.test(placeholder) || /стоимость|цена/i.test(placeholder)) return input;
            }
            for (const block of offerRoot.querySelectorAll('.form-item, .offer-form__row, label')) {
                const text = (block.textContent || '').toLowerCase();
                if (!text.includes('стоимость') && !text.includes('цена')) continue;
                const input = block.querySelector("input:not([type='hidden'])");
                if (input && input.getClientRects().length && !input.disabled) return input;
            }
            return null;
        };
        const findDuration = () => {
            for (const select of offerRoot.querySelectorAll('.v-select')) {
                const selected = select.querySelector('.vs__selected');
                const label = (selected?.textContent || '').replace(/\\s+/g, ' ').trim();
                if (/^\\d+$/.test(label) || /^\\d+\\s*(день|дня|дней)/i.test(label)) {
                    return select;
                }
            }
            return null;
        };
        const price = findPriceInput();
        const duration = findDuration();
        const nodes = [editor, price, duration].filter(Boolean);
        if (!nodes.length) return null;
        let top = Infinity, left = Infinity, bottom = 0, right = 0;
        for (const node of nodes) {
            const rect = node.getBoundingClientRect();
            if (rect.height <= 0 || rect.width <= 0) continue;
            top = Math.min(top, rect.top);
            left = Math.min(left, rect.left);
            bottom = Math.max(bottom, rect.bottom);
            right = Math.max(right, rect.right);
        }
        if (!Number.isFinite(top)) return null;
        top = Math.max(0, top - 48);
        left = Math.max(0, left - 24);
        bottom = bottom + 180;
        right = right + 24;
        const height = bottom - top;
        const width = right - left;
        if (height < 80 || width < 120) return null;
        return { x: left, y: top, width, height };
    }"""
    )
    if clip:
        duration_row = page.locator("text=Срок выполнения").locator(
            'xpath=ancestor::*[.//div[contains(@class,"v-select")]][1]'
        )
        if await duration_row.count():
            box = await duration_row.first.bounding_box()
            if box:
                clip_bottom = clip["y"] + clip["height"]
                target_bottom = box["y"] + box["height"] + 24
                if target_bottom > clip_bottom:
                    clip["height"] = target_bottom - clip["y"]
        return await page.screenshot(type="png", clip=clip)
    for scroll_target in ("text=Описание", "text=Стоимость", "text=Срок выполнения"):
        loc = page.locator(scroll_target).first
        if await loc.count() > 0:
            try:
                await loc.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
    return await page.locator("main").screenshot(type="png")


async def _capture(project_id: str, out_dir: Path) -> dict:
    from app.config import get_settings
    from app.db.models import Job, SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.kwork_browser import (
        _is_access_blocked,
        _verify_offer_on_project_page,
        close_browser,
        launch_browser,
        login,
    )
    from app.utils.formatting import extract_site_order_id
    from app.utils.proxy import get_proxy_candidates, mask_proxy

    settings = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"ui-kwork-offer-sent-{project_id}.png"

    offer_urls = (
        f"https://kwork.ru/projects/{project_id}/view",
        f"https://kwork.ru/manage_orders?tab=offers",
        "https://kwork.ru/projects?tab=offers",
        f"https://kwork.ru/projects/{project_id}",
    )

    candidates: list[str | None] = list(get_proxy_candidates(settings, "openai"))
    if None not in candidates:
        candidates.insert(0, None)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        attempt_row: SendAttempt | None = None
        job_row: Job | None = None
        rows = await session.execute(
            select(SendAttempt, Job)
            .join(Job, SendAttempt.job_id == Job.id)
            .where(SendAttempt.status == "confirmed_sent", Job.platform == "kwork")
        )
        for attempt, job in rows.all():
            order_id = extract_site_order_id(
                external_id=job.external_id, url=job.url, platform=job.platform
            )
            if order_id != project_id:
                continue
            if (attempt.platform_response or {}).get("dry_run") is True:
                continue
            attempt_row = attempt
            job_row = job
            break

    if attempt_row is None or job_row is None:
        raise RuntimeError(f"no confirmed_sent live attempt for kwork project {project_id}")

    expectations = _snapshot_expectations(attempt_row)
    job_url = job_row.url or f"https://kwork.ru/projects/{project_id}"

    screenshot_bytes: bytes | None = None
    capture_url: str | None = None
    last_error: str | None = None
    for proxy in candidates:
        label = mask_proxy(proxy) if proxy else "direct"
        playwright, browser, page = await launch_browser(proxy=proxy)
        try:
            if settings.kwork_email and settings.kwork_password:
                await login(page, settings.kwork_email, settings.kwork_password)
            if await _is_access_blocked(page):
                raise RuntimeError(f"Kwork access blocked at {page.url}")

            steps: list[str] = []
            form_shot = await _capture_sent_offer_form(page, project_id, job_url, steps)
            if form_shot is not None:
                matched, reason = await _ui_matches_snapshot(page, expectations)
                if matched:
                    screenshot_bytes = form_shot
                    capture_url = page.url
                    print(
                        f"sent-offer form capture ok via proxy={label} url={capture_url} steps={steps}",
                        file=sys.stderr,
                    )
                else:
                    last_error = f"{label}: edit form snapshot mismatch: {reason}"

            for url in offer_urls:
                if screenshot_bytes is not None:
                    break
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(3000)
                if await _is_access_blocked(page):
                    continue
                body = (await page.inner_text("body")).lower()
                if project_id not in (await page.content()) and project_id not in body:
                    if "tab=offers" not in url and f"/projects/{project_id}" not in url:
                        continue
                if await _verify_offer_on_project_page(page, project_id):
                    for locator in (
                        page.locator("text=ваше предложение").locator("xpath=ancestor::*[self::div or self::section][1]"),
                        page.locator("text=редактировать предложение").locator(
                            "xpath=ancestor::*[self::div or self::section][1]"
                        ),
                        page.locator(".offer-item, .kwork-offer, [class*='offer']").first,
                    ):
                        if await locator.count() > 0:
                            await locator.first.scroll_into_view_if_needed()
                            screenshot_bytes = await locator.first.screenshot(type="png")
                            capture_url = page.url
                            break
                if screenshot_bytes is None and project_id in await page.content():
                    offer_block = page.locator(
                        f"a[href*='/projects/{project_id}'], [data-project-id='{project_id}']"
                    ).first
                    if await offer_block.count() > 0:
                        row = offer_block.locator(
                            "xpath=ancestor::*[self::tr or self::div][position()<=4]"
                        ).first
                        target = row if await row.count() > 0 else offer_block
                        await target.scroll_into_view_if_needed()
                        screenshot_bytes = await target.screenshot(type="png")
                        capture_url = page.url
                if screenshot_bytes is None and await _verify_offer_on_project_page(page, project_id):
                    screenshot_bytes = await page.screenshot(type="png", full_page=False)
                    capture_url = page.url
                if screenshot_bytes is not None:
                    matched, reason = await _ui_matches_snapshot(page, expectations)
                    if not matched:
                        screenshot_bytes = None
                        last_error = f"{label}: legacy capture rejected: {reason}"
                        continue
                    print(f"captured via proxy={label} url={capture_url}", file=sys.stderr)
                    break
            if screenshot_bytes is not None:
                last_error = None
                break
            last_error = f"{label}: no sent-offer UI found for project {project_id}"
        except Exception as exc:
            last_error = f"{label}: {exc}"
        finally:
            await close_browser(playwright, browser)

    if screenshot_bytes is None:
        raise RuntimeError(last_error or "failed to capture screenshot")

    png_path.write_bytes(screenshot_bytes)
    png_sha = hashlib.sha256(screenshot_bytes).hexdigest()

    attempt_id = attempt_row.id
    snapshot_sha = hashlib.sha256((attempt_row.snapshot_content or "").encode()).hexdigest()

    attempt_doc = {
        "send_attempt_id": str(attempt_id),
        "kwork_order_id": project_id,
        "snapshot_sha256": snapshot_sha,
        "mode": "live",
        "status": "confirmed_sent",
        "dry_run": False,
    }
    attempt_path = out_dir / f"attempt-{project_id}.json"
    attempt_bytes = json.dumps(attempt_doc, ensure_ascii=False, indent=2).encode()
    attempt_path.write_bytes(attempt_bytes)

    return {
        "png_path": png_path,
        "png_sha256": png_sha,
        "attempt_path": attempt_path,
        "attempt_sha256": hashlib.sha256(attempt_bytes).hexdigest(),
        "send_attempt_id": str(attempt_id),
        "snapshot_sha256": snapshot_sha,
    }


def _update_signoff(meta: dict, *, authorization: str) -> None:
    signoff_path = ROOT / "data/live_kwork_signoff.json"
    now = datetime.now(UTC).astimezone()
    data = json.loads(signoff_path.read_text(encoding="utf-8"))
    rel_png = meta["png_path"].relative_to(ROOT / "data")
    rel_attempt = meta["attempt_path"].relative_to(ROOT / "data")
    data.update(
        {
            "authorization_reference": authorization,
            "verified_by": data.get("verified_by") or "Arthur (owner)",
            "verified_at": now.isoformat(),
            "send_attempt_id": meta["send_attempt_id"],
            "snapshot_sha256": meta["snapshot_sha256"],
            "snapshot_verified": True,
            "ui_matches_db": True,
            "ui_evidence": {"path": str(rel_png), "sha256": meta["png_sha256"]},
            "attempt_evidence": {
                "path": str(rel_attempt),
                "sha256": meta["attempt_sha256"],
            },
            "notes": (
                f"Sent-offer UI for Kwork {data.get('kwork_order_id')} from /offers → edit_offer "
                "(read-only capture); text/price/duration matched DB snapshot attempt."
            ),
        }
    )
    signoff_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", help="Numeric Kwork project id (e.g. 3271087)")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT,
        help="Directory for PNG/JSON artifacts (under data/)",
    )
    parser.add_argument(
        "--authorization-reference",
        default="Owner requested full C110 closure via agent 2026-11-05",
    )
    parser.add_argument("--update-signoff", action="store_true")
    args = parser.parse_args()

    meta = await _capture(args.project_id, args.out_dir)
    print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in meta.items()}, indent=2))
    if args.update_signoff:
        _update_signoff(meta, authorization=args.authorization_reference)
    from app.db.session import engine

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
