"""Exact Kwork inbox message → job resolution (no substring false positives)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db.models import Client, Job, Proposal
from app.utils.formatting import extract_site_order_id

_ACTIVE_JOB_STATUSES = ("sent", "scored", "approved")


@dataclass
class KworkJobMatchResult:
    job: Job | None = None
    proposal: Proposal | None = None
    needs_context: bool = False
    candidate_jobs: list[Job] = field(default_factory=list)


def kwork_project_id_matches(job: Job, project_id: str) -> bool:
    needle = str(project_id).strip()
    if not needle or not needle.isdigit():
        return False
    site_id = extract_site_order_id(
        external_id=job.external_id,
        url=job.url,
        platform=job.platform,
    )
    return site_id == needle


def kwork_username_matches_client(client: Client | None, username: str) -> bool:
    if not client or not username.strip():
        return False
    normalized = username.strip().lower()
    name = (client.name or "").strip().lower()
    if name == normalized:
        return True
    external = (client.external_id or "").strip().lower()
    if external in {normalized, f"/user/{normalized}", f"https://kwork.ru/user/{normalized}"}:
        return True
    return bool(external.endswith(f"/{normalized}") and "/user/" in external)


async def best_proposal_for_reply(session, job_id: uuid.UUID) -> Proposal | None:
    result = await session.execute(
        select(Proposal).where(Proposal.job_id == job_id).order_by(Proposal.created_at.desc())
    )
    proposals = list(result.scalars().all())
    if not proposals:
        return None

    priority = {"sent": 0, "approved": 1, "edited": 2, "draft": 3}
    proposals.sort(
        key=lambda proposal: (
            priority.get(proposal.status, 9),
            -(proposal.created_at.timestamp() if proposal.created_at else 0),
        )
    )
    return proposals[0]


async def find_kwork_job_for_message(
    session,
    *,
    username: str,
    project_id: str | None,
) -> KworkJobMatchResult:
    if project_id:
        result = await session.execute(select(Job).where(Job.platform == "kwork"))
        matches = [job for job in result.scalars().all() if kwork_project_id_matches(job, project_id)]
        if len(matches) == 1:
            proposal = await best_proposal_for_reply(session, matches[0].id)
            return KworkJobMatchResult(job=matches[0], proposal=proposal)
        if len(matches) > 1:
            return KworkJobMatchResult(needs_context=True, candidate_jobs=matches)
        # An explicit, unmatched project is stronger evidence than a username match.
        return KworkJobMatchResult(needs_context=True)

    if username:
        result = await session.execute(
            select(Job)
            .where(
                Job.platform == "kwork",
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(Job.created_at.desc())
        )
        matches: list[Job] = []
        for job in result.scalars().all():
            client = await session.get(Client, job.client_id) if job.client_id else None
            if kwork_username_matches_client(client, username):
                matches.append(job)

        if len(matches) == 1:
            proposal = await best_proposal_for_reply(session, matches[0].id)
            return KworkJobMatchResult(job=matches[0], proposal=proposal)
        if len(matches) > 1:
            return KworkJobMatchResult(needs_context=True, candidate_jobs=matches)

    return KworkJobMatchResult()
