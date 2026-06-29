import structlog
from langgraph.graph import END, StateGraph

from app.agents.chat_agent import chat_node
from app.agents.filter_agent import filter_node
from app.agents.learning_agent import learning_node
from app.agents.proposal_agent import proposal_node
from app.agents.scoring_agent import scoring_node
from app.config import get_settings
from app.schemas.agent_state import JobPilotState

logger = structlog.get_logger(__name__)


def _min_score_threshold() -> int:
    return get_settings().min_score_threshold


def route_after_filter(state: JobPilotState) -> str:
    if state.get("error"):
        return "end"
    if state.get("is_relevant"):
        return "score"
    return "end"


def route_after_score(state: JobPilotState) -> str:
    score = state.get("score", 0)
    if score >= _min_score_threshold():
        return "propose"
    return "end"


def route_after_approval(state: JobPilotState) -> str:
    status = state.get("approval_status", "pending")
    if status == "approved" or status == "edited":
        return "send"
    if status == "skipped":
        return "learn_skip"
    return "wait"


def route_after_send(state: JobPilotState) -> str:
    if state.get("client_message"):
        return "chat"
    return "learn"


async def approval_node(state: JobPilotState) -> dict:
    """Mark proposal as pending human approval via Telegram."""
    logger.info(
        "JobPilot AI awaiting Telegram approval",
        job_id=state.get("job_id"),
        score=state.get("score"),
    )
    return {"approval_status": state.get("approval_status", "pending")}


async def send_node(state: JobPilotState) -> dict:
    """Send approved proposal to platform."""
    from app.services.proposal_sender import ProposalSender

    sender = ProposalSender()
    content = state.get("edited_proposal") or state.get("proposal_content", "")
    job_id = state.get("job_id", "")
    proposal_id = state.get("proposal_id", "")

    success, error = await sender.send(job_id, proposal_id, content)
    logger.info("JobPilot AI proposal sent", job_id=job_id, success=success, error=error)
    return {
        "approval_status": "sent" if success else "pending",
        "outcome_status": "sent" if success else "draft",
        "send_error": error or "",
    }


async def learn_skip_node(state: JobPilotState) -> dict:
    return {"outcome_status": "ignored", "approval_status": "skipped"}


def build_jobpilot_graph():
    """
    JobPilot AI LangGraph pipeline:

    Job → FilterAgent → ScoringAgent → ProposalAgent → Telegram Approval
         → Send → ChatAgent → LearningAgent
    """
    builder = StateGraph(JobPilotState)

    builder.add_node("filter", filter_node)
    builder.add_node("score", scoring_node)
    builder.add_node("propose", proposal_node)
    builder.add_node("approval", approval_node)
    builder.add_node("send", send_node)
    builder.add_node("chat", chat_node)
    builder.add_node("learn", learning_node)
    builder.add_node("learn_skip", learn_skip_node)

    builder.set_entry_point("filter")

    builder.add_conditional_edges(
        "filter",
        route_after_filter,
        {"score": "score", "end": END},
    )
    builder.add_conditional_edges(
        "score",
        route_after_score,
        {"propose": "propose", "end": END},
    )
    builder.add_edge("propose", "approval")
    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {
            "send": "send",
            "learn_skip": "learn_skip",
            "wait": END,
        },
    )
    builder.add_conditional_edges(
        "send",
        route_after_send,
        {"chat": "chat", "learn": "learn"},
    )
    builder.add_edge("chat", "learn")
    builder.add_edge("learn", END)
    builder.add_edge("learn_skip", "learn")

    return builder


def compile_jobpilot_graph(checkpointer=None):
    builder = build_jobpilot_graph()
    if checkpointer:
        return builder.compile(checkpointer=checkpointer, interrupt_before=["send"])
    return builder.compile(interrupt_before=["send"])
