import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types
from google.genai.types import Content, Part

from tools.callbacks import after_tool_callback, before_tool_callback

load_dotenv()

_MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8088/mcp")


def _before_rag_agent(callback_context: CallbackContext) -> Content | None:
    cached = callback_context.state.get("rag_result")
    if cached and "ANSWER NOT FOUND" not in cached:
        print("[before_agent] rag_agent - cache hit, skipping")
        return Content(parts=[Part(text=cached)])
    return None


def _after_rag_agent(callback_context: CallbackContext) -> None:
    result = callback_context.state.get("rag_result", "")
    status = "FOUND" if result and "ANSWER NOT FOUND" not in result else "ANSWER NOT FOUND"
    print(f"[after_agent] rag_agent - {status}")


rag_agent = LlmAgent(
    name="rag_agent",
    model="gemini-2.5-flash",
    description=(
        "Knowledge retrieval agent. Searches the approved-answer knowledge base "
        "before any expensive BigQuery or analysis operation runs."
    ),
    instruction=(
        """You are the knowledge retrieval agent for the Agentic BI system. Your sole job is to check whether a similar question has already been answered.

Step 1: Call search_knowledge_base with the user's exact question.

Step 2: Look at the results:
- If count > 0 and results are relevant: respond with is_cached=True followed by the answer text from the results.
- If count == 0 or results are not relevant: respond with exactly "ANSWER NOT FOUND" and nothing else."""
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=_MCP_URL,
                timeout=30.0,
                sse_read_timeout=60.0,
            )
        )
    ],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
        top_p=0.95,
        max_output_tokens=1024,
    ),
    output_key="rag_result",
    before_agent_callback=_before_rag_agent,
    after_agent_callback=_after_rag_agent,
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
)
