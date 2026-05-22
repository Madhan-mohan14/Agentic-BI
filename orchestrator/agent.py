import os

import httpx
from a2a.types import AgentCard
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from google.genai.types import Content

from sub_agents.rag_agent.agent import rag_agent
from sub_agents.analysis_agent.agent import analysis_agent
from sub_agents.data_agent.agent import data_agent
from sub_agents.research_agent.agent import research_agent
from tools.observability import setup_tracing

load_dotenv()
setup_tracing()


def _load_audit_card(base_url: str) -> AgentCard:
    """Fetch agent card from Cloud Run and override url to the public Cloud Run URL.

    to_a2a() defaults host='localhost', so the card advertises http://localhost:8080.
    RemoteA2aAgent uses card.url for actual RPC calls — we patch it here to the
    real Cloud Run URL before wiring it into the agent.
    """
    try:
        resp = httpx.get(f"{base_url}/.well-known/agent.json", timeout=15.0)
        card_data = resp.json()
        card_data["url"] = f"{base_url}/"
        card = AgentCard.model_validate(card_data)
        print(f"[orchestrator] audit card fetched OK — rpc_url patched to {base_url}/")
        return card
    except Exception as exc:
        print(f"[orchestrator] audit card prefetch failed ({exc}), using minimal card")
        return AgentCard(name="audit_agent", url=f"{base_url}/", version="1.0.0")


# ── Audit Agent — consumed from Cloud Run via A2A ────────────────────────────
_AUDIT_A2A_URL = os.environ.get(
    "AUDIT_A2A_URL",
    "https://audit-agent-service-492257799932.us-central1.run.app",
)
audit_agent = RemoteA2aAgent(
    name="audit_agent",
    agent_card=_load_audit_card(_AUDIT_A2A_URL),
    use_legacy=False,
)


def _before_orchestrator(callback_context: CallbackContext) -> Content | None:
    """Inject session_id into state so orchestrator can pass it to audit_agent."""
    try:
        session_id = callback_context._invocation_context.session.id
        callback_context.state["session_id"] = session_id
    except Exception:
        callback_context.state.setdefault("session_id", "")
    return None



# ── Orchestrator ─────────────────────────────────────────────────────────────
orchestrator = LlmAgent(
    name="orchestrator",
    model="gemini-2.5-flash",
    description="Root orchestrator for the Agentic BI system.",
    tools=[],
    sub_agents=[rag_agent, analysis_agent, data_agent, research_agent, audit_agent],
    before_agent_callback=_before_orchestrator,
    instruction=(
        """You are the orchestrator of an Agentic Business Intelligence system. You route every analytics question through three agents in fixed order using transfer_to_agent. You never answer data questions yourself.

For greetings or capability questions (hello, hi, what can you do): reply directly without calling any agent.

For every analytics question, follow these three steps in order without skipping any:

Step 1: Call transfer_to_agent with agent_name="rag_agent". Pass the user's question.

Step 2: Call transfer_to_agent with the specialist that matches the question:
  - Top customers, SQL queries, rankings, breakdowns by category/country/brand/gender, return rates, inventory → agent_name="data_agent"
  - Revenue totals, order counts, AOV, KPI over a time period, anomaly detection, metric spikes → agent_name="analysis_agent"
  - Why something happened, market trends, external causes, industry context → agent_name="research_agent"

Step 3: Call transfer_to_agent with agent_name="audit_agent". Pass both the original user question and the full answer from Step 2.

After audit_agent responds:
  - APPROVED: return the answer to the user exactly as given.
  - NEEDS_REGENERATION: call the same specialist from Step 2 once more, then call audit_agent again.
  - ESCALATED_TO_HITL: tell the user their question is under human review."""
    ),
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
        top_p=0.95,
        max_output_tokens=2048,
    ),
)

# MODEL ARMOR — GOVERN pillar (wire after deploy by setting MODEL_ARMOR_TEMPLATE_ID env var)
# from tools.model_armor import model_armor_interceptor, model_armor_response_interceptor
# if os.environ.get("MODEL_ARMOR_TEMPLATE_ID"):
#     orchestrator.before_agent_callback = model_armor_interceptor
#     orchestrator.after_model_callback = model_armor_response_interceptor

# ADK discovers this name when running `adk web` from the project root
root_agent = orchestrator
