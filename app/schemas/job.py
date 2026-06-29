from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ClientInfo(BaseModel):
    name: str | None = None
    external_id: str | None = None
    rating: float | None = None
    reviews_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedJob(BaseModel):
    platform: str
    external_id: str
    title: str
    description: str
    budget_min: float | None = None
    budget_max: float | None = None
    budget_currency: str = "USD"
    skills: list[str] = Field(default_factory=list)
    deadline: datetime | None = None
    url: str | None = None
    client: ClientInfo | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: UUID
    platform: str
    title: str
    description: str
    budget_min: float | None
    budget_max: float | None
    score: int | None
    status: str
    is_relevant: bool | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProposalResponse(BaseModel):
    id: UUID
    job_id: UUID
    content: str
    execution_plan: str | None
    timeline: str | None
    status: str

    model_config = {"from_attributes": True}


class OutcomeRequest(BaseModel):
    status: str = Field(description="sent | ignored | replied | hired")
    notes: str | None = None


class ChatRequest(BaseModel):
    job_id: UUID
    message: str


class ChatResponse(BaseModel):
    intent: str
    reply: str
