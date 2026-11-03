"""Kwork buyer statistics are read from a unique buyer block, not the description."""

import os

import pytest
from playwright.async_api import async_playwright

from app.scrapers.kwork import KworkScraper, _buyer_profile_path

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_BROWSER_TESTS') != '1', reason='explicit offline Chromium opt-in required'
)


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=os.environ.get('JOBPILOT_TEST_CHROMIUM') or None
        )
        context = await browser.new_context(service_workers='block')
        await context.route('**/*', lambda route: route.abort())
        page = await context.new_page()
        yield page
        await context.close()
        await browser.close()


def buyer_block(*, extra=''):
    return '''<div class="want-payer-statistic"><div>
       <div class="want-card__statistic-item"><span>Покупатель:</span><a href="https://kwork.ru/user/buyer">buyer</a></div>
       <div class="want-card__statistic-item"><span>Размещено проектов на бирже:</span> 1</div>
       <div class="want-card__statistic-item"><span>Нанято:</span> 0%</div>''' + extra + '</div></div>'


async def test_description_cannot_spoof_buyer_stats(page):
    await page.set_content('<div class="want-card"><div class="description">Размещено проектов на бирже: 99 Нанято: 99%</div>' + buyer_block() + '</div>')
    result = await KworkScraper.__new__(KworkScraper)._read_card_buyer(page.locator('.want-card'))
    assert result == ('buyer', '/user/buyer', {'projects_posted': 1, 'hire_percent': 0, 'stats_source': 'kwork_buyer_block_v1'})


@pytest.mark.parametrize('mode', ['missing', 'duplicate_block', 'duplicate_link', 'duplicate_stats', 'malformed_duplicate'])
async def test_ambiguous_buyer_information_fails_closed(page, mode):
    block = buyer_block(extra=(
        '<div class="want-card__statistic-item">Нанято: 99%</div>' if mode == 'duplicate_stats'
        else '<div class="want-card__statistic-item">Нанято: неизвестно</div>' if mode == 'malformed_duplicate' else ''
    ))
    if mode == 'missing':
        block = ''
    if mode == 'duplicate_block':
        block += buyer_block()
    if mode == 'duplicate_link':
        block = block.replace('</a></div>', '</a><a href="/user/other">other</a></div>', 1)
    await page.set_content('<div class="want-card">' + block + '</div>')
    name, path, stats = await KworkScraper.__new__(KworkScraper)._read_card_buyer(page.locator('.want-card'))
    if mode in {'duplicate_stats', 'malformed_duplicate'}:
        assert name == 'buyer' and path == '/user/buyer' and 'hire_percent' not in stats
    else:
        assert (name, path, stats) == (None, None, {})


@pytest.mark.parametrize(('url', 'expected'), [
    ('/user/buyer', '/user/buyer'), ('https://kwork.ru/user/buyer', '/user/buyer'),
    ('https://evil.example/user/buyer', None), ('//evil.example/user/buyer', None),
    ('https://kwork.ru.evil.example/user/buyer', None),
])
async def test_buyer_profile_path_rejects_other_hosts(url, expected):
    assert _buyer_profile_path(url) == expected


@pytest.mark.skipif(os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required')
async def test_offline_listing_to_reassessment_and_new_review(page, monkeypatch):
    import json
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy import select

    from app.agents.filter_agent import FilterAgent
    from app.config import Settings
    from app.db.models import Job, Proposal, SendAttempt, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services import job_pipeline
    from app.services.kwork_reassessment import reassess_kwork_job
    from app.services.review_reissue import reissue_review

    number = str(uuid.uuid4().int)[:16]
    description = 'Исправить одну ошибку в Python API, предоставить краткое описание изменения и проверку. ' * 3
    block = buyer_block().replace(' 1</div>', ' 5</div>').replace(' 0%</div>', ' 50%</div>')
    await page.set_content(
        '<div class="want-card"><div class="wants-card__header-title">'
        f'<a href="/projects/{number}">Исправить API</a></div>'
        f'<div class="wants-card__description-text">{description}</div>'
        '<div class="wants-card__price">1000 ₽</div><div class="want-card__informers"></div>'
        + block + '</div>'
    )
    scraper = KworkScraper.__new__(KworkScraper)
    scraper._read_buyer_history = AsyncMock(return_value={})
    normalized = await scraper._extract_kwork_card(page.locator('.want-card'), page)
    assert normalized and normalized.client.metadata['stats_source'] == 'kwork_buyer_block_v1'
    monkeypatch.setattr(job_pipeline, 'get_memory_store', lambda: SimpleNamespace(
        store_job_embedding=AsyncMock(return_value=False)))
    job_id = await job_pipeline.JobPipelineService().save_normalized_job(normalized)
    proposal_id, pending_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.status = 'approved'
        session.add(Proposal(id=proposal_id, job_id=job_id, content='Предлагаю исправить ошибку.',
                             version=1, status='approved'))
        session.add(TelegramPending(id=pending_id, job_id=job_id, proposal_id=proposal_id,
                                    chat_id=100, message_id=44, status='approved'))
        await session.commit()
    agent = FilterAgent.__new__(FilterAgent)
    agent._settings = Settings(_env_file=None, REVIEW_FIRST_MODE=True)
    agent._excluded = []
    agent._llm = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({
        'relevant': True, 'reason': 'simple fix', 'estimated_hours': 1,
        'estimated_calendar_days': 1, 'scope_clear': True, 'complexity': 'simple',
        'minimum_price_rub': 500,
    })))
    assert (await reassess_kwork_job(job_id, evaluator=agent.run))['status'] == 'accepted'
    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID='100', TELEGRAM_ADMIN_USER_ID='5')
    review = await reissue_review('proposal', pending_id, chat_id=100, user_id=5, settings=cfg)
    assert review.action == 'queued' and review.pending_id != pending_id
    async with AsyncSessionLocal() as session:
        assert (await session.get(TelegramPending, review.pending_id)).status == 'pending'
        assert await session.scalar(select(SendAttempt.id).where(SendAttempt.job_id == job_id)) is None
    agent._llm.complete.assert_awaited_once()
