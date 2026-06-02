import os
import sys

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types

from tools.callbacks import after_tool_callback, before_tool_callback

load_dotenv()

_MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8088/mcp")


def _after_rag_agent(callback_context: CallbackContext) -> None:
    result = callback_context.state.get("rag_result", "")
    status = "FOUND" if result and "ANSWER NOT FOUND" not in result else "ANSWER NOT FOUND"
    print(f"[after_agent] rag_agent - {status}", file=sys.stderr)


rag_agent = LlmAgent(
    name="rag_agent",
    model="gemini-2.5-flash",
    description=(
        "Knowledge retrieval agent. Searches the approved-answer knowledge base "
        "before any expensive BigQuery or analysis operation runs."
    ),
    instruction=(
        """You are the knowledge cache agent. Your ONLY job is to call search_knowledge_base and return what it finds.

ALWAYS call search_knowledge_base with the user's question first. Never skip this.

After the tool returns:
- If count is 0: respond with exactly the word: ANSWER NOT FOUND
- If count is 1 or more: respond with exactly this format and nothing else:
    is_cached=True
    <paste the full text of results[0].result here>

Do not judge whether the result is relevant. Do not paraphrase. Do not add any text.
The RAG engine already filtered for relevance — trust it."""
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=_MCP_URL,
                timeout=30.0,
                sse_read_timeout=60.0,
            ),
            tool_filter=["search_knowledge_base"],
        )
    ],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
        top_p=0.95,
        max_output_tokens=1024,
    ),
    output_key="rag_result",
    after_agent_callback=_after_rag_agent,
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
)
