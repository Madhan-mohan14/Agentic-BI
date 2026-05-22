import logging

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

logger = logging.getLogger(__name__)

VALID_TABLES = ["orders", "order_items", "inventory_items", "users", "products"]
VALID_DATASETS = ["thelook_ecommerce", "ga4_obfuscated_sample_ecommerce"]


async def before_tool_callback(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
) -> dict | None:
    """
    Runs before every tool call on any agent that registers it.

    Rules enforced:
    - detect_anomaly table arg is corrected silently if invalid
    - get_schema_context dataset arg is validated strictly
    - log_resolution is blocked if score < 0.8

    Returns:
        None to let the call proceed (with possibly corrected args).
        A dict to block the call and return that dict as the result instead.
    """
    # Rule 1: validate + silently correct table name for detect_anomaly
    if tool.name == "detect_anomaly":
        table = args.get("table", "")
        if table not in VALID_TABLES:
            logger.warning("before_tool_callback: invalid table '%s' → corrected to 'orders'", table)
            args["table"] = "orders"

    # Rule 2: validate dataset name for get_schema_context strictly
    if tool.name == "get_schema_context":
        dataset = args.get("dataset", "")
        if dataset not in VALID_DATASETS:
            return {
                "error": f"Unknown dataset: '{dataset}'.",
                "valid_datasets": VALID_DATASETS,
            }

    # Rule 3: block log_resolution if audit score too low
    if tool.name == "log_resolution":
        score = args.get("score", 0.0)
        if score < 0.8:
            return {
                "error": "Resolution not logged.",
                "reason": f"Score {score:.2f} is below the 0.8 threshold.",
                "action": "Re-run analysis or trigger HITL review.",
            }

    return None  # proceed normally


async def after_tool_callback(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """
    Runs after every tool call on any agent that registers it.

    Rules enforced:
    - execute_sql: pass through unchanged
    - detect_anomaly with very high threshold → add advisory note
    - search_knowledge_base cache miss → flag to proceed to BigQuery
    - track user format preference in session state

    Returns:
        None to use the original response unchanged.
        A dict to replace the tool response with a modified version.
    """
    # Rule 1: pass execute_sql response through unchanged — errors surface to the agent as-is
    if tool.name == "execute_sql":
        return None

    # Rule 2: anomaly threshold too high → add advisory note
    if tool.name == "detect_anomaly":
        if not tool_response.get("is_anomaly") and args.get("threshold", 2.0) > 3.0:
            tool_response["note"] = (
                "Threshold was very high. Try threshold=2.0 for standard detection."
            )
            return tool_response

    # Rule 3: RAG cache miss → tell agent to proceed to BigQuery
    if tool.name == "search_knowledge_base":
        if tool_response.get("count", 0) == 0:
            return {
                "results": [],
                "count": 0,
                "cache_miss": True,
                "action": "No past analyses found. Proceed to Data Agent for a fresh query.",
            }

    # Rule 4: track user output format preference in session state
    if tool.name in ("generate_kpi_summary", "create_report_job"):
        query_text = str(args.get("sql", "") or args.get("metrics", "") or args.get("format", "")).lower()
        if any(kw in query_text for kw in ("table", "csv", "excel")):
            tool_context.state["user:preferred_format"] = "table"
        elif any(kw in query_text for kw in ("paragraph", "summary", "text")):
            tool_context.state["user:preferred_format"] = "text"

    return None  # use original response unchanged


# ── Agent-level callbacks (shared, usable by any agent) ───────────────────────

async def before_agent_callback(callback_context: CallbackContext) -> Content | None:
    """
    Generic before-agent hook. Logs which agent is starting.
    Individual agents can override this with their own specialized version.
    Returns None to let the agent run normally.
    """
    agent_name = getattr(callback_context, "agent_name", "unknown")
    logger.info("[before_agent] %s starting", agent_name)
    return None


async def after_agent_callback(callback_context: CallbackContext) -> Content | None:
    """
    Generic after-agent hook. Logs completion and output length.
    Returns None to keep the agent's original output.
    """
    agent_name = getattr(callback_context, "agent_name", "unknown")
    logger.info("[after_agent] %s completed", agent_name)
    return None
