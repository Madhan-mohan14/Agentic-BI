import logging
import os
import re
import sys

import httpx
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
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

logger = logging.getLogger(__name__)

# Patterns that indicate prompt injection or abuse attempts
_INJECTION_PATTERNS = re.compile(
    r"(ignore previous instructions|disregard (your|all) instructions|"
    r"you are now|act as (a|an|the)|forget (you are|your role)|"
    r"new persona|system prompt|bypass|jailbreak|DAN mode)",
    re.IGNORECASE,
)

# Hard block: requests that have no business intelligence purpose
_BLOCK_PATTERNS = re.compile(
    r"\b(hack|exploit|malware|ransomware|phishing|ddos|sql.?inject|"
    r"drop table|delete from|truncate|rm -rf|shell|exec\(|eval\()\b",
    re.IGNORECASE,
)


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
        print(f"[orchestrator] audit card fetched OK — rpc_url patched to {base_url}/", file=sys.stderr)
        return card
    except Exception as exc:
        print(f"[orchestrator] audit card prefetch failed ({exc}), using minimal card", file=sys.stderr)
        return AgentCard(
            name="audit_agent",
            url=f"{base_url}/",
            version="1.0.0",
            description="Audit and scoring agent for BI query validation.",
            capabilities=AgentCapabilities(),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[AgentSkill(id="audit", name="audit", description="Score and log BI query results.", tags=["audit", "scoring"])],
        )


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
    """Inject session_id into state before each agent turn for BQ analytics tracking."""
    try:
        session_id = callback_context._invocation_context.session.id
        callback_context.state["session_id"] = session_id
    except Exception:
        callback_context.state.setdefault("session_id", "")
    return None


def _safety_model_filter(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    """
    Model Armor substitute — runs before every LLM API call.
    Screens the full prompt context for injection attempts and hard-blocked terms.
    Returns LlmResponse to short-circuit the model call when a violation is found.
    """
    # Collect all user-role text from the request being sent to the model
    user_text_parts: list[str] = []
    for content in (llm_request.contents or []):
        if getattr(content, "role", None) == "user":
            for part in (content.parts or []):
                text = getattr(part, "text", None)
                if text:
                    user_text_parts.append(text)
    user_text = " ".join(user_text_parts)

    if not user_text:
        return None

    if _BLOCK_PATTERNS.search(user_text):
        logger.warning("[safety] hard-blocked request: %.120s", user_text)
        return LlmResponse(
            content=Content(
                role="model",
                parts=[types.Part(text=(
                    "I can only help with business intelligence and ecommerce analytics. "
                    "That request falls outside my scope."
                ))],
            )
        )

    if _INJECTION_PATTERNS.search(user_text):
        logger.warning("[safety] injection attempt detected: %.120s", user_text)
        return LlmResponse(
            content=Content(
                role="model",
                parts=[types.Part(text=(
                    "I detected an attempt to alter my instructions. "
                    "I'm a BI assistant — please ask a business or analytics question."
                ))],
            )
        )

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
    before_model_callback=_safety_model_filter,
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
        safety_settings=[
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            ),
        ],
    ),
)

# ADK discovers this name when running `adk web` from the project root
root_agent = orchestrator

from google.adk.apps import App
from tools.plugin import BIAgentPlugin, BigQueryAnalyticsPlugin

app = App(
    root_agent=root_agent,
    name="orchestrator",
    plugins=[BIAgentPlugin(), BigQueryAnalyticsPlugin()],
)
