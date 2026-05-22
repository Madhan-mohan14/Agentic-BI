import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types

from tools.callbacks import after_tool_callback, before_tool_callback

load_dotenv()

_MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8088/mcp")

data_agent = LlmAgent(
    name="data_agent",
    model="gemini-2.5-flash",
    description=(
        "Data analyst agent for an ecommerce business. Calls get_schema_context "
        "first, then the appropriate analysis tool based on the user query."
    ),
    instruction=(
        """You are the data agent for an ecommerce BI system. You answer SQL-based questions by querying bigquery-public-data.thelook_ecommerce.

Step 1: Call get_schema_context(dataset='thelook_ecommerce') to confirm table and column names.

Step 2: Call execute_sql with a valid BigQuery SELECT query. Rules:
  - Always filter WHERE status = 'Complete' for revenue or order totals.
  - For customer rankings use u.email to identify customers, join orders o to users u on o.user_id = u.id.
  - Never use CURRENT_DATE() or CURRENT_TIMESTAMP() — the dataset is historical.
  - For date filters: DATE(o.created_at) >= DATE_SUB((SELECT MAX(DATE(created_at)) FROM `bigquery-public-data.thelook_ecommerce.orders`), INTERVAL N DAY)
  - Always include LIMIT.

Step 3: Write a clear English answer with the results and the SQL you ran. Stop — do not call any other agent."""
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=_MCP_URL,
                timeout=30.0,
                sse_read_timeout=120.0,
            )
        )
    ],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
        top_p=0.95,
        max_output_tokens=2048,
    ),
    output_key="data_result",
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
)
