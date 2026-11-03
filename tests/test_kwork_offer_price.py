import pytest

from app.utils.formatting import (
    resolve_kwork_offer_price,
    sync_kwork_proposal_price,
)


def test_resolve_kwork_offer_price_respects_ai_minimum() -> None:
    assessment = {"minimum_price_rub": 1500}
    price = resolve_kwork_offer_price(1000, 3000, assessment, 20.0)
    assert price == 1500


def test_sync_kwork_proposal_price_replaces_wrong_amount() -> None:
    text = (
        "Добрый день!\n\n"
        "Сделаю парсер на Python.\n"
        "Моя цена за выполнение составит 1 500 ₽.\n\n"
        "1. Какой формат выгрузки?"
    )
    synced = sync_kwork_proposal_price(text, 1200)
    assert "1 500" not in synced
    assert "1 200 ₽" in synced
    assert synced.count("Моя цена за выполнение") == 1


def test_price_replacement_preserves_preceding_lines():
    text = "Привет\nСделаю задачу\nСтоимость 1500 ₽."
    result = sync_kwork_proposal_price(text, 500)
    assert "Привет\nСделаю задачу" in result
    assert "500 ₽" in result


def test_conflicting_inline_price_is_not_silently_erased():
    with pytest.raises(ValueError):
        sync_kwork_proposal_price("Моя цена за выполнение составит 500 ₽.\nЗа работу возьму 3 000 ₽.", 500)


def test_review_rejects_price_conflicting_with_assessment():
    import uuid

    from app.db.models import Job, Proposal
    from app.services.approval_snapshot import proposal_card_payload
    from app.utils.formatting import kwork_text_price_matches

    job_id = uuid.uuid4()
    job = Job(id=job_id, platform="kwork", external_id="test", title="t", description="d",
              budget_min=1000, budget_max=3000, budget_currency="RUB",
              raw_data={"opportunity_assessment": {"minimum_price_rub": 1500}})
    proposal = Proposal(id=uuid.uuid4(), job_id=job_id, version=1,
                        content="Выполню работу за 500 ₽.")
    payload = proposal_card_payload(job, proposal)
    assert payload["content"] == proposal.content and payload["offer_price"] == 1500
    assert not kwork_text_price_matches(proposal.content, payload["offer_price"])
