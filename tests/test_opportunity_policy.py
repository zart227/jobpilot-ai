import pytest

from app.llm.call_usage import LLMCallUsage
from app.services.opportunity_policy import (
    REVIEW_WEIGHTS,
    client_rejection,
    kwork_assessment_rejection,
    kwork_opportunity_fingerprint,
    parse_buyer_history,
    parse_kwork_card_buyer_stats,
)


@pytest.mark.parametrize('job', [
    {}, {'client_rating': 5, 'client_reviews': 100},
    {'client_rating': 4.7, 'client_completed_paid_orders': 5},
    {'client_rating': 5, 'client_completed_paid_orders': 0},
    {'client_rating': float('nan'), 'client_completed_paid_orders': 5},
])
def test_unproven_or_poor_buyers_rejected(job):
    assert client_rejection(job, 4.8, 1)


def test_proven_buyer_accepted():
    assert client_rejection({'client_rating': 4.9, 'client_completed_paid_orders': 3}, 4.8, 1) is None


def _filter_agent_mock_llm(*, complete_return: str):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    return SimpleNamespace(
        complete=AsyncMock(return_value=complete_return),
        consume_last_call_usage=lambda: LLMCallUsage(resolved_model="mock", provider_kind="mock"),
    )


def test_legacy_kwork_statistics_are_not_accepted_as_buyer_block_evidence():
    assert client_rejection({'platform': 'kwork', 'client_projects_posted': 99, 'client_hire_percent': 99}, 4.8, 1)


def test_seller_reviews_and_closed_projects_are_not_payment_evidence():
    assert parse_buyer_history('Рейтинг продавца: 5/5 Отзывы: 100 Закрыто проектов: 10') == {
        'rating': None, 'completed_paid_orders': None,
    }


def test_explicit_buyer_evidence():
    assert parse_buyer_history('Рейтинг покупателя: 4,9/5\nВыполнено и оплачено заказов: 12') == {
        'rating': 4.9, 'completed_paid_orders': 12,
    }


@pytest.mark.parametrize(('text', 'expected'), [
    ('Размещено проектов на бирже: 60\nНанято: 56%', {'projects_posted': 60, 'hire_percent': 56}),
    ('Размещено проектов на бирже: 8\nНанято: 0%', {'projects_posted': 8, 'hire_percent': 0}),
    ('Размещено проектов на бирже: 1', {'projects_posted': 1}),
    ('Отзывы: 56%\nНанято: 101%', {}),
    ('Размещено проектов на бирже: 1 234\nНанято: 10%', {'projects_posted': 1234, 'hire_percent': 10}),
    ('Размещено проектов на бирже: 5\n50 предложений\nНанято: 56%', {'projects_posted': 5, 'hire_percent': 56}),
])
def test_listing_buyer_statistics_are_distinct_from_paid_orders(text, expected):
    assert parse_kwork_card_buyer_stats(text) == expected


@pytest.mark.parametrize(('projects', 'hired', 'accepted'), [
    (5, 50, True), (60, 56, True), (4, 100, False), (8, 0, False),
    (5, 49, False), (None, 50, False), (5, None, False), (5, 101, False),
])
def test_kwork_buyer_listing_threshold(projects, hired, accepted):
    job = {'platform': 'kwork', 'client_projects_posted': projects, 'client_hire_percent': hired, 'client_stats_source': 'kwork_buyer_block_v1'}
    assert (client_rejection(job, 4.8, 1) is None) is accepted


@pytest.mark.asyncio
@pytest.mark.parametrize(('hours', 'days', 'complexity', 'price', 'budget', 'accepted'), [
    (3, 1, 'simple', 500, 1000, True), (3.1, 1, 'simple', 500, 1000, False),
    (2, 1, 'moderate', 500, 1000, False), (2, 1, 'simple', None, 1000, False),
    (2, 1, 'simple', 1500, 1000, False), (None, 1, 'simple', 500, 1000, False),
    (2, None, 'simple', 500, 1000, False), (2, 2, 'simple', 500, 1000, False),
])
async def test_kwork_scope_and_minimum_price_must_be_confirmed(hours, days, complexity, price, budget, accepted):
    import json
    from types import SimpleNamespace

    from app.agents.filter_agent import FilterAgent

    agent = FilterAgent.__new__(FilterAgent)
    agent._settings = SimpleNamespace(
        review_first_mode=True, review_max_hours=8, client_min_rating=4.8,
        client_min_paid_orders=1, developer_skills='Python', developer_name='Dev',
        developer_excluded_skills='', developer_hourly_rate=50, developer_bio='',
    )
    agent._excluded = []
    agent._llm = _filter_agent_mock_llm(
        complete_return=json.dumps({
        'relevant': True, 'reason': 'matches', 'estimated_hours': hours, 'scope_clear': True,
        'complexity': complexity, 'minimum_price_rub': price, 'estimated_calendar_days': days,
    }))
    result = await agent.run({'job_data': {
        'platform': 'kwork', 'title': 'Fix Python bug', 'description': 'Fix one bug in Python code', 'budget_max': budget,
        'client_projects_posted': 5, 'client_hire_percent': 50, 'client_stats_source': 'kwork_buyer_block_v1',
    }})
    assert result['is_relevant'] is accepted
    assert result['opportunity_assessment']['minimum_price_rub'] == price


def test_assessed_price_and_one_day_are_bound_to_approval_snapshot():
    from types import SimpleNamespace

    from app.services.approval_snapshot import proposal_card_payload

    job = SimpleNamespace(
        id='job', platform='kwork', title='Fix bug', url='https://example.test', budget_min=1000,
        budget_max=2000, budget_currency='RUB',
        raw_data={'opportunity_assessment': {'minimum_price_rub': 700, 'estimated_hours': 2}},
    )
    proposal = SimpleNamespace(id='proposal', version=1, content='text')
    payload = proposal_card_payload(job, proposal)
    assert payload['offer_price'] == 700
    assert payload['duration_days'] == 1


def test_kwork_assessment_rejects_legacy_or_changed_job_and_buyer():
    job = {
        'id': 'job', 'platform': 'kwork', 'title': 'Fix bug', 'description': 'Fix one bug',
        'budget_min': 1000.0, 'budget_max': 2000.0, 'budget_currency': 'RUB',
        'client_projects_posted': 5, 'client_hire_percent': 50, 'client_stats_source': 'kwork_buyer_block_v1',
    }
    assessment = {
        'relevant': True, 'assessment_id': 'e48e15f8-6d76-4076-9631-78bf927c5719', 'source_fingerprint': kwork_opportunity_fingerprint(job),
        'estimated_hours': 2, 'estimated_calendar_days': 1, 'scope_clear': True,
        'complexity': 'simple', 'minimum_price_rub': 700,
    }
    assert kwork_assessment_rejection(job, assessment) is None
    assert kwork_assessment_rejection(job, None) == 'assessment_required'
    assert kwork_assessment_rejection({**job, 'description': 'Changed'}, assessment) == 'assessment_stale'
    assert kwork_assessment_rejection({**job, 'client_hire_percent': 49}, assessment) == 'buyer_not_eligible'
    assert kwork_assessment_rejection({**job, 'client_projects_posted': 6}, assessment) == 'assessment_stale'
    assert kwork_assessment_rejection({**job, 'client_id': 'different-buyer'}, assessment) == 'assessment_stale'
    assert kwork_assessment_rejection(job, {**assessment, 'estimated_calendar_days': 2}) == 'assessment_ineligible'


@pytest.mark.asyncio
@pytest.mark.parametrize('description', ['', 'Fix Python bug', 'x' * 12001])
async def test_kwork_incomplete_or_oversized_description_is_rejected_without_llm(description):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agents.filter_agent import FilterAgent

    agent = FilterAgent.__new__(FilterAgent)
    agent._settings = SimpleNamespace(
        review_first_mode=True, review_max_hours=3, client_min_rating=4.8,
        client_min_paid_orders=1, developer_skills='Python', developer_name='Dev',
        developer_excluded_skills='', developer_hourly_rate=50, developer_bio='',
    )
    agent._excluded = []
    agent._llm = SimpleNamespace(
        complete=AsyncMock(),
        consume_last_call_usage=lambda: LLMCallUsage(resolved_model="mock", provider_kind="mock"),
    )
    result = await agent.run({'job_data': {
        'platform': 'kwork', 'title': 'Fix Python bug', 'description': description,
        'client_projects_posted': 5, 'client_hire_percent': 50, 'client_stats_source': 'kwork_buyer_block_v1',
    }})
    assert result['is_relevant'] is False
    agent._llm.complete.assert_not_awaited()


def test_review_strategy_prioritizes_ease_over_money():
    assert sum(REVIEW_WEIGHTS.values()) == pytest.approx(1)
    assert REVIEW_WEIGHTS['complexity'] > REVIEW_WEIGHTS['budget']

@pytest.mark.asyncio
@pytest.mark.parametrize('hours,clear,accepted', [(2, True, True), (12, True, False), (None, True, False), (2, False, False)])
async def test_filter_enforces_small_known_scope(hours, clear, accepted):
    import json
    from types import SimpleNamespace

    from app.agents.filter_agent import FilterAgent

    agent = FilterAgent.__new__(FilterAgent)
    agent._settings = SimpleNamespace(
        review_first_mode=True, review_max_hours=8, client_min_rating=4.8,
        client_min_paid_orders=1, developer_skills='Python', developer_name='Dev',
        developer_excluded_skills='', developer_hourly_rate=50, developer_bio='',
    )
    agent._excluded = []
    agent._llm = _filter_agent_mock_llm(complete_return=json.dumps({
        'relevant': True, 'reason': 'matches', 'estimated_hours': hours, 'scope_clear': clear,
    }))
    result = await agent.run({'job_data': {
        'platform': 'test', 'title': 'Fix Python bug',
        'client_rating': 4.9, 'client_completed_paid_orders': 3,
    }})
    assert result['is_relevant'] is accepted


def test_legacy_assessment_without_revision_is_rejected():
    job = {'id': 'j', 'platform': 'kwork', 'client_id': 'buyer',
           'client_projects_posted': 5, 'client_hire_percent': 50,
           'client_stats_source': 'kwork_buyer_block_v1'}
    assessment = {'relevant': True, 'source_fingerprint': kwork_opportunity_fingerprint(job),
                  'estimated_hours': 1, 'estimated_calendar_days': 1, 'scope_clear': True,
                  'complexity': 'simple', 'minimum_price_rub': 500}
    assert kwork_assessment_rejection(job, assessment) == 'assessment_required'
