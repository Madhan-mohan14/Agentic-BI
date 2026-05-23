import os

import httpx
from a2a.types import AgentCard
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.agent_tool import AgentTool
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
    "https://your-audit-agent-service.us-central1.run.app",
)
audit_agent = RemoteA2aAgent(
    name="audit_agent",
    agent_card=_load_audit_card(_AUDIT_A2A_URL),
    use_legacy=False,
)


def _before_orchestrator(callback_context: CallbackContext) -> Content | None:
    """Inject session_id into state before each orchestrator turn."""
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
    tools=[
        AgentTool(agent=rag_agent),
        AgentTool(agent=data_agent),
        AgentTool(agent=analysis_agent),
        AgentTool(agent=research_agent),
        AgentTool(agent=audit_agent),
    ],
    before_agent_callback=_before_orchestrator,
    instruction=(
        """You are the root orchestrator of an Agentic Business Intelligence system.
You never answer data or analytics questions yourself. You always call your agent tools.

For greetings, thanks, or questions about your capabilities: respond directly in one line. Do not call any tool.

For every analytics or business question, call these agents in sequence. Do not stop early.

STEP 1 — Call rag_agent with the user's exact question as the request.
Read the result:
- If it starts with "is_cached=True": take the text after that line as the cached answer. Skip Step 2. Go to Step 3 and include is_cached=True.
- If it is "ANSWER NOT FOUND": continue to Step 2.

STEP 2 — Call exactly one specialist (only when Step 1 returned "ANSWER NOT FOUND"):
- Customer rankings, top-N lists, breakdowns by country/category/brand/gender, return rates, inventory → call data_agent
- Revenue totals, order counts, AOV, KPI trends, anomaly or spike detection → call analysis_agent
- WHY something happened, market context, external causes → call research_agent
Take the specialist's full response as the answer.

STEP 3 — Call audit_agent with this exact text as the request:
  QUESTION: <original user question>
  ANSWER: <full answer from Step 1 or Step 2>
  is_cached=True   ← include this line only if the answer came from the Step 1 cache

When audit_agent responds:
- Starts with "APPROVED" → return the answer to the user exactly as given after "APPROVED ... — ".
- Starts with "NEEDS_REGENERATION" → call the same Step 2 specialist once more, then call audit_agent again with the new answer.
- Starts with "ESCALATED_TO_HITL" → tell the user: "Your question has been escalated to human review."

Never respond to the user with data yourself. Always complete all three steps and reach APPROVED or ESCALATED_TO_HITL first."""
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
