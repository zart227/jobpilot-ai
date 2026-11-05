"""Per-completion usage captured at the provider (for pipeline stage ledger / C115)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMCallUsage:
    resolved_model: str
    provider_kind: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    def as_ledger_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "resolved_model": self.resolved_model,
            "model_provider": self.provider_kind,
        }
        if self.prompt_tokens is not None:
            fields["prompt_tokens"] = self.prompt_tokens
        if self.completion_tokens is not None:
            fields["completion_tokens"] = self.completion_tokens
        if self.total_tokens is not None:
            fields["total_tokens"] = self.total_tokens
        if self.cost_usd is not None:
            fields["cost_usd"] = round(float(self.cost_usd), 8)
        return fields


def usage_from_openai_compat_usage(model: str, provider_kind: str, usage: Any) -> LLMCallUsage | None:
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    cost = getattr(usage, "cost", None)
    if cost is None and isinstance(usage, dict):
        prompt = usage.get("prompt_tokens", prompt)
        completion = usage.get("completion_tokens", completion)
        total = usage.get("total_tokens", total)
        cost = usage.get("cost", cost)
    if prompt is None and completion is None and total is None and cost is None:
        return None
    if total is None and prompt is not None and completion is not None:
        total = int(prompt) + int(completion)
    cost_usd = float(cost) if cost is not None else None
    return LLMCallUsage(
        resolved_model=model,
        provider_kind=provider_kind,
        prompt_tokens=int(prompt) if prompt is not None else None,
        completion_tokens=int(completion) if completion is not None else None,
        total_tokens=int(total) if total is not None else None,
        cost_usd=cost_usd,
    )
