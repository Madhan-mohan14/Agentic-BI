import logging
import os
import time
from datetime import datetime, timezone

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

logger = logging.getLogger(__name__)

# ── BigQuery analytics (lazy singleton, writes on every sub-agent tool call) ──

_BQ_DATASET = "agent_analytics"
_BQ_TABLE = "tool_events"

_MCP_TOOLS = {
    "execute_sql", "detect_anomaly", "generate_kpi_summary",
    "create_report_job", "get_schema_context", "log_resolution",
    "search_knowledge_base",
}
_TOOLBOX_TOOLS = {"get_top_products", "get_revenue_by_period", "get_inventory_anomalies"}
_SUB_AGENT_TOOLS = {"rag_agent", "data_agent", "analysis_agent", "research_agent", "audit_agent"}

def _layer(tool_name: str) -> str:
    if tool_name in _MCP_TOOLS:
        return "MCP"
    if tool_name in _TOOLBOX_TOOLS:
        return "TOOLBOX"
    if tool_name in _SUB_AGENT_TOOLS:
        return "SUB_AGENT"
    return "BUILTIN"

_bq_client = None
_bq_table_ref: str | None = None
_bq_ready = False

def _ensure_bq() -> bool:
    global _bq_client, _bq_table_ref, _bq_ready
    if _bq_ready:
        return True
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project:
        return False
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=project)
        dataset_ref = f"{project}.{_BQ_DATASET}"
        table_ref = f"{dataset_ref}.{_BQ_TABLE}"
        try:
            client.get_dataset(dataset_ref)
        except Exception:
            from google.cloud.bigquery import Dataset
            ds = Dataset(dataset_ref)
            ds.location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
            client.create_dataset(ds, exists_ok=True)
        try:
            client.get_table(table_ref)
        except Exception:
            from google.cloud.bigquery import Table, SchemaField
            schema = [
                SchemaField("event_time", "TIMESTAMP", mode="REQUIRED"),
                SchemaField("session_id", "STRING",    mode="NULLABLE"),
                SchemaField("tool_name",  "STRING",    mode="REQUIRED"),
                SchemaField("layer",      "STRING",    mode="REQUIRED"),
                SchemaField("latency_ms", "INTEGER",   mode="REQUIRED"),
                SchemaField("success",    "BOOL",      mode="REQUIRED"),
                SchemaField("error_msg",  "STRING",    mode="NULLABLE"),
                SchemaField("args_keys",  "STRING",    mode="NULLABLE"),
            ]
            client.create_table(Table(table_ref, schema=schema), exists_ok=True)
        _bq_client = client
        _bq_table_ref = table_ref
        _bq_ready = True
        logger.info("[callbacks_bq] ready — writing to %s", table_ref)
        return True
    except Exception as exc:
        logger.warning("[callbacks_bq] init failed (%s) — events will log-only", exc)
        return False

def _bq_write(tool_name: str, session_id: str, latency_ms: int,
              success: bool, error_msg: str | None, args_keys: str) -> None:
    if not _ensure_bq():
        return
    row = {
        "event_time": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "tool_name": tool_name,
        "layer": _layer(tool_name),
        "latency_ms": latency_ms,
        "success": success,
        "error_msg": error_msg,
        "args_keys": args_keys,
    }
    try:
        errors = _bq_client.insert_rows_json(_bq_table_ref, [row])
        if errors:
            logger.warning("[callbacks_bq] insert errors: %s", errors)
        else:
            logger.info("[callbacks_bq] wrote row: tool=%s layer=%s latency=%dms ok=%s",
                        tool_name, row["layer"], latency_ms, success)
    except Exception as exc:
        logger.warning("[callbacks_bq] insert failed: %s", exc)

_call_start: dict[str, float] = {}

VALID_TABLES = ["orders", "order_items", "inventory_items"]
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
    _call_start[tool.name] = time.monotonic()

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
        elapsed_ms = round((time.monotonic() - _call_start.pop(tool.name, time.monotonic())) * 1000)
        _bq_write(
            tool_name=tool.name,
            session_id=str(tool_context.state.get("session_id", "")),
            latency_ms=elapsed_ms,
            success="error" not in tool_response,
            error_msg=tool_response.get("error") if isinstance(tool_response, dict) else None,
            args_keys=",".join(str(k) for k in args.keys()),
        )
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
        # McpToolset wraps the response as {'content': [{'type': 'text', 'text': '<json>'}]}
        # Parse the nested JSON to get the actual count.
        import datetime as _dt
        import json as _json
        import re as _re
        count = tool_response.get("count", None)
        parsed = None
        if count is None:
            content_list = tool_response.get("content", [])
            if content_list and isinstance(content_list[0], dict) and content_list[0].get("type") == "text":
                try:
                    parsed = _json.loads(content_list[0]["text"])
                    count = parsed.get("count", 0)
                except Exception:
                    count = 0
            else:
                count = 0
        if count == 0:
            return {
                "results": [],
                "count": 0,
                "cache_miss": True,
                "action": "No past analyses found. Proceed to Data Agent for a fresh query.",
            }
        # 24hr TTL: if every result lacks a fresh "Logged: YYYY-MM-DD" stamp, treat as miss
        if parsed is None:
            parsed = tool_response
        today = _dt.date.today()
        all_stale = True
        for item in parsed.get("results", []):
            text = item.get("result", "") if isinstance(item, dict) else str(item)
            m = _re.search(r"Logged: (\d{4}-\d{2}-\d{2})", text)
            if m:
                try:
                    logged = _dt.date.fromisoformat(m.group(1))
                    if (today - logged).days < 1:
                        all_stale = False
                        break
                except ValueError:
                    pass
        if all_stale:
            return {
                "results": [],
                "count": 0,
                "cache_miss": True,
                "action": "Cached analyses are older than 24 hours. Proceed to Data Agent for a fresh query.",
            }

    # Rule 4: track user output format preference in session state
    if tool.name in ("generate_kpi_summary", "create_report_job"):
        query_text = str(args.get("sql", "") or args.get("metrics", "") or args.get("format", "")).lower()
        if any(kw in query_text for kw in ("table", "csv", "excel")):
            tool_context.state["user:preferred_format"] = "table"
        elif any(kw in query_text for kw in ("paragraph", "summary", "text")):
            tool_context.state["user:preferred_format"] = "text"

    # Write BigQuery analytics row for every tool call that reaches this point
    elapsed_ms = round((time.monotonic() - _call_start.pop(tool.name, time.monotonic())) * 1000)
    session_id = str(tool_context.state.get("session_id", ""))
    _bq_write(
        tool_name=tool.name,
        session_id=session_id,
        latency_ms=elapsed_ms,
        success="error" not in tool_response,
        error_msg=tool_response.get("error") if isinstance(tool_response, dict) else None,
        args_keys=",".join(str(k) for k in args.keys()),
    )

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
