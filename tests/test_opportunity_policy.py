import pytest

from app.services.opportunity_policy import REVIEW_WEIGHTS, client_rejection, parse_buyer_history


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


def test_seller_reviews_and_closed_projects_are_not_payment_evidence():
    assert parse_buyer_history('Рейтинг продавца: 5/5 Отзывы: 100 Закрыто проектов: 10') == {
        'rating': None, 'completed_paid_orders': None,
    }


def test_explicit_buyer_evidence():
    assert parse_buyer_history('Рейтинг покупателя: 4,9/5\nВыполнено и оплачено заказов: 12') == {
        'rating': 4.9, 'completed_paid_orders': 12,
    }


def test_review_strategy_prioritizes_ease_over_money():
    assert sum(REVIEW_WEIGHTS.values()) == pytest.approx(1)
    assert REVIEW_WEIGHTS['complexity'] > REVIEW_WEIGHTS['budget']

@pytest.mark.asyncio
@pytest.mark.parametrize('hours,clear,accepted', [(2, True, True), (12, True, False), (None, True, False), (2, False, False)])
async def test_filter_enforces_small_known_scope(hours, clear, accepted):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agents.filter_agent import FilterAgent

    agent = FilterAgent.__new__(FilterAgent)
    agent._settings = SimpleNamespace(
        review_first_mode=True, review_max_hours=8, client_min_rating=4.8,
        client_min_paid_orders=1, developer_skills='Python', developer_name='Dev',
        developer_excluded_skills='', developer_hourly_rate=50, developer_bio='',
    )
    agent._excluded = []
    agent._llm = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({
        'relevant': True, 'reason': 'matches', 'estimated_hours': hours, 'scope_clear': clear,
    })))
    result = await agent.run({'job_data': {
        'platform': 'test', 'title': 'Fix Python bug',
        'client_rating': 4.9, 'client_completed_paid_orders': 3,
    }})
    assert result['is_relevant'] is accepted
