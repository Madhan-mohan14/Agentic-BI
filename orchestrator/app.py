import os

from dotenv import load_dotenv
from google.adk.apps import App
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService

from .agent import root_agent
from tools.plugin import BIAgentPlugin, BigQueryAnalyticsPlugin

load_dotenv()

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
_IS_LOCAL = os.environ.get("LOCAL_DEV", "false").lower() == "true"


def _build_session_service():
    if _IS_LOCAL or not _PROJECT:
        print("[app] Using InMemorySessionService (LOCAL_DEV=true or no project set)")
        return InMemorySessionService()
    try:
        svc = VertexAiSessionService(project=_PROJECT, location=_LOCATION)
        print(f"[app] Using VertexAiSessionService (project={_PROJECT}, location={_LOCATION})")
        return svc
    except Exception as exc:
        print(f"[app] VertexAiSessionService unavailable ({exc}) — falling back to InMemory")
        return InMemorySessionService()


def _build_memory_service():
    # TODO: swap to VertexAiMemoryBankService once class name verified in installed ADK version.
    # from google.adk.memory import VertexAiMemoryBankService
    # if not _IS_LOCAL and _PROJECT:
    #     return VertexAiMemoryBankService(project=_PROJECT, location=_LOCATION)
    return InMemoryMemoryService()


class AgenticBIApp:
    """
    Wrapper that wires session/memory services and plugins to the Runner.

    Plugin note: BIAgentPlugin + BigQueryAnalyticsPlugin are active only when
    this Runner is used (programmatic runs, Agent Engine path). adk web creates
    its own runner and ignores these plugins — that is a known ADK constraint.
    For adk web with Vertex AI sessions use:
        adk web --session_service_uri="agentengine://<AGENT_ENGINE_ID>"
    """

    def __init__(self):
        self._runner = Runner(
            agent=root_agent,
            app_name="orchestrator",
            session_service=_build_session_service(),
            memory_service=_build_memory_service(),
            plugins=[BIAgentPlugin(), BigQueryAnalyticsPlugin()],
        )
        print("[app] AgenticBIApp initialized — plugins: BIAgentPlugin, BigQueryAnalyticsPlugin")

    @property
    def runner(self):
        return self._runner


coordinator = AgenticBIApp()
app = App(root_agent=root_agent, name="orchestrator")
