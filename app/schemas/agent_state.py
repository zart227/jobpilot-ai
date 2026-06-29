from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


class JobData(TypedDict, total=False):
    id: str
    platform: str
    external_id: str
    title: str
    description: str
    budget_min: float | None
    budget_max: float | None
    budget_currency: str
    skills: list[str]
    deadline: str | None
    url: str | None
    client_name: str | None
    client_rating: float | None
    client_reviews: int


class JobPilotState(TypedDict, total=False):
    """LangGraph state schema for JobPilot AI pipeline."""

    job_data: JobData
    job_id: str
    proposal_id: str
    is_relevant: bool
    relevance_reason: str
    score: int
    score_breakdown: dict[str, float]
    proposal_content: str
    execution_plan: str
    timeline: str
    approval_status: Literal["pending", "approved", "edited", "skipped", "sent"]
    edited_proposal: str
    client_message: str
    chat_intent: str
    chat_reply: str
    outcome_status: str
    send_error: str
    reward: int
    learning_notes: str
    error: str
    messages: Annotated[list, add_messages]
