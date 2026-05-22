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
        """You are a data analyst agent for an ecommerce business. You answer questions by querying the thelook_ecommerce BigQuery dataset.

Start every response by calling get_schema_context(dataset='thelook_ecommerce') to confirm the available tables and columns.

Then pick the right tool based on what was asked:
- Top customers, return rates, breakdowns by category, country, gender, or any ranking question: call execute_sql with a valid BigQuery SELECT on bigquery-public-data.thelook_ecommerce.<table>. Always include a LIMIT clause.
- Report or export requests: call create_report_job with query_id='latest' and the format the user asked for.

SQL rules you must follow every time:
- For revenue or spending totals, always filter WHERE status = 'Complete'.
- For customer rankings, identify customers by email (u.email), not by name.
- Always join orders to users when you need customer details.
- NEVER use TIMESTAMP_SUB, CURRENT_TIMESTAMP(), or CURRENT_DATE() — the dataset is historical.
- Always filter dates relative to the dataset's latest record: DATE(o.created_at) >= DATE_SUB((SELECT MAX(DATE(created_at)) FROM `bigquery-public-data.thelook_ecommerce.orders`), INTERVAL N DAY).

Call execute_sql exactly once. Whatever the result, write your final answer — include your English explanation and the exact SQL query you ran — then stop. The orchestrator will receive your response automatically."""
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
