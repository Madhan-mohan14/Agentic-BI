import logging
import os
import time
from datetime import datetime, timezone

from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from google.genai.types import Content

logger = logging.getLogger(__name__)

_BQ_DATASET = "agent_analytics"
_BQ_TABLE = "tool_events"

# MCP tools served by the remote FastMCP server
_MCP_TOOLS = {
    "execute_sql", "detect_anomaly", "generate_kpi_summary",
    "create_report_job", "get_schema_context", "log_resolution",
    "search_knowledge_base",
}

# MCP Toolbox tools (Layer 2)
_TOOLBOX_TOOLS = {"get_top_products", "get_revenue_by_period", "get_inventory_anomalies"}


def _resolve_layer(tool_name: str) -> str:
    if tool_name in _MCP_TOOLS:
        return "MCP"
    if tool_name in _TOOLBOX_TOOLS:
        return "TOOLBOX"
    if tool_name == "google_search":
        return "BUILTIN"
    return "SUB_AGENT"


class BIAgentPlugin(BasePlugin):
    """
    Handles infrastructure failures so agents degrade gracefully instead of crashing.

    Callbacks handle wrong logic (bad args, empty results).
    This plugin handles crashes (timeouts, quota errors, downed services).
    """

    def __init__(self) -> None:
        super().__init__(name="bi_agent_plugin")

    async def on_tool_error(
        self,
        tool: BaseTool,
        args: dict,
        error: Exception,
        tool_context: ToolContext,
    ) -> dict | None:
        """
        Called when a tool raises an unhandled exception.

        Returns:
            A dict to use as the tool result instead of crashing.
            None to re-raise the original error.
        """
        error_str = str(error)
        logger.error("Tool error in '%s': %s", tool.name, error_str)

        # BigQuery timeout → return last cached result if available
        if "Timeout" in error_str or "DeadlineExceeded" in error_str:
            cached = tool_context.state.get("last_bq_result")
            if cached:
                return {
                    "rows": cached,
                    "source": "cache",
                    "warning": "BigQuery timed out. Returning last cached result.",
                }
            return {
                "rows": [],
                "error": "BigQuery timed out and no cache is available.",
                "action": "Try a narrower date range to reduce query size.",
            }

        # MCP Toolbox server not running
        if "Connection refused" in error_str or "ConnectionError" in error_str:
            return {
                "error": "MCP Toolbox server is not reachable.",
                "action": "Ensure toolbox.exe is running on localhost:5000.",
                "tool": tool.name,
            }

        # RAG Engine unavailable → skip cache, proceed to BigQuery
        if "RAG" in error_str or "corpus" in error_str.lower():
            return {
                "results": [],
                "count": 0,
                "warning": "RAG Engine unavailable. Skipping cache, proceeding to BigQuery.",
            }

        # Quota exceeded on any tool
        if "quota" in error_str.lower() or "429" in error_str:
            return {
                "error": "Tool quota exceeded.",
                "action": "Wait 60 seconds and retry.",
                "retry_after_seconds": 60,
            }

        return None  # unknown error — re-raise

    async def on_model_error(
        self,
        error: Exception,
        invocation_context: InvocationContext,
    ) -> LlmResponse | None:
        """
        Called when the Gemini LLM call itself fails.

        Returns:
            An LlmResponse to return to the user instead of crashing.
            None to re-raise the original error.
        """
        error_str = str(error)
        logger.error("Model error: %s", error_str)

        if "quota" in error_str.lower() or "RESOURCE_EXHAUSTED" in error_str:
            return LlmResponse(
                content=Content(
                    role="model",
                    parts=[types.Part(text=(
                        "I'm temporarily rate-limited by the AI model. "
                        "Your query has been noted — please retry in 30 seconds."
                    ))],
                )
            )

        if "503" in error_str or "overloaded" in error_str.lower():
            return LlmResponse(
                content=Content(
                    role="model",
                    parts=[types.Part(text=(
                        "The AI model is temporarily overloaded. Please retry in 10 seconds."
                    ))],
                )
            )

        return None  # unknown model error — re-raise

    async def after_tool_call(
        self,
        tool: BaseTool,
        args: dict,
        tool_response: dict,
        tool_context: ToolContext,
    ) -> None:
        """
        Caches the last successful BigQuery result for timeout fallback.
        Called after every successful tool execution — no return value.
        """
        if tool.name == "execute_sql" and tool_response.get("rows"):
            tool_context.state["last_bq_result"] = tool_response["rows"]


class BigQueryAnalyticsPlugin(BasePlugin):
    """
    Writes tool execution events to BigQuery for the GOVERN + OPTIMIZE pillars.

    Every tool call (success or error) is written as a row to:
        <project>.agent_analytics.tool_events

    The table is created on first use if it doesn't exist. Rows include:
    timestamp, session_id, tool_name, layer, latency_ms, success, error_msg,
    args_keys. optimize.py reads this table to pull traces with score < 0.7.

    Also keeps session-state provenance for Langfuse/OTel traces (unchanged
    from the stub so downstream consumers are not broken).
    """

    _SCHEMA = [
        {"name": "event_time",  "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "session_id",  "type": "STRING",    "mode": "NULLABLE"},
        {"name": "tool_name",   "type": "STRING",    "mode": "REQUIRED"},
        {"name": "layer",       "type": "STRING",    "mode": "REQUIRED"},
        {"name": "latency_ms",  "type": "INTEGER",   "mode": "REQUIRED"},
        {"name": "success",     "type": "BOOL",      "mode": "REQUIRED"},
        {"name": "error_msg",   "type": "STRING",    "mode": "NULLABLE"},
        {"name": "args_keys",   "type": "STRING",    "mode": "NULLABLE"},
    ]

    def __init__(self) -> None:
        super().__init__(name="bigquery_analytics_plugin")
        self._call_start: dict[str, float] = {}
        self._project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self._bq = None
        self._table_ref = None
        self._table_ready = False
        if self._project:
            self._init_bq()

    def _init_bq(self) -> None:
        try:
            from google.cloud import bigquery
            self._bq = bigquery.Client(project=self._project)
            dataset_ref = f"{self._project}.{_BQ_DATASET}"
            table_ref = f"{dataset_ref}.{_BQ_TABLE}"
            self._table_ref = table_ref
            # create dataset if missing
            try:
                self._bq.get_dataset(dataset_ref)
            except Exception:
                from google.cloud.bigquery import Dataset
                ds = Dataset(dataset_ref)
                ds.location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
                self._bq.create_dataset(ds, exists_ok=True)
                logger.info("[bq_analytics] created dataset %s", dataset_ref)
            # create table if missing
            try:
                self._bq.get_table(table_ref)
            except Exception:
                from google.cloud.bigquery import Table, SchemaField
                schema = [SchemaField(f["name"], f["type"], mode=f["mode"]) for f in self._SCHEMA]
                tbl = Table(table_ref, schema=schema)
                self._bq.create_table(tbl, exists_ok=True)
                logger.info("[bq_analytics] created table %s", table_ref)
            self._table_ready = True
            logger.info("[bq_analytics] ready — writing to %s", table_ref)
        except Exception as exc:
            logger.warning("[bq_analytics] init failed (%s) — events will log-only", exc)

    def _write_row(self, row: dict) -> None:
        if not self._table_ready and self._project:
            self._init_bq()
        if not self._table_ready or self._bq is None:
            return
        try:
            errors = self._bq.insert_rows_json(self._table_ref, [row])
            if errors:
                logger.warning("[bq_analytics] insert errors: %s", errors)
        except Exception as exc:
            logger.warning("[bq_analytics] insert failed: %s", exc)

    async def before_tool_call(
        self,
        tool: BaseTool,
        args: dict,
        tool_context: ToolContext,
    ) -> dict | None:
        self._call_start[tool.name] = time.monotonic()
        return None

    async def after_tool_call(
        self,
        tool: BaseTool,
        args: dict,
        tool_response: dict,
        tool_context: ToolContext,
    ) -> None:
        elapsed_ms = round(
            (time.monotonic() - self._call_start.pop(tool.name, time.monotonic())) * 1000
        )
        layer = _resolve_layer(tool.name)
        session_id = tool_context.state.get("session_id", "")
        row = {
            "event_time": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "tool_name": tool.name,
            "layer": layer,
            "latency_ms": elapsed_ms,
            "success": "error" not in tool_response,
            "error_msg": None,
            "args_keys": ",".join(args.keys()),
        }
        self._write_row(row)
        # keep session-state provenance for OTel/Langfuse
        provenance = {**row, "args_keys": list(args.keys())}
        tool_context.state["tool_provenance"] = provenance
        history: list = tool_context.state.get("tool_provenance_history", [])
        history.append(provenance)
        tool_context.state["tool_provenance_history"] = history[-20:]
        logger.info(
            "[bq_analytics] tool=%s layer=%s latency_ms=%d success=%s",
            tool.name, layer, elapsed_ms, row["success"],
        )

    async def on_tool_error(
        self,
        tool: BaseTool,
        args: dict,
        error: Exception,
        tool_context: ToolContext,
    ) -> dict | None:
        elapsed_ms = round(
            (time.monotonic() - self._call_start.pop(tool.name, time.monotonic())) * 1000
        )
        layer = _resolve_layer(tool.name)
        session_id = tool_context.state.get("session_id", "")
        row = {
            "event_time": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "tool_name": tool.name,
            "layer": layer,
            "latency_ms": elapsed_ms,
            "success": False,
            "error_msg": str(error)[:500],
            "args_keys": ",".join(args.keys()),
        }
        self._write_row(row)
        provenance = {**row, "args_keys": list(args.keys())}
        tool_context.state["tool_provenance"] = provenance
        history: list = tool_context.state.get("tool_provenance_history", [])
        history.append(provenance)
        tool_context.state["tool_provenance_history"] = history[-20:]
        logger.warning(
            "[bq_analytics] tool=%s layer=%s latency_ms=%d ERROR=%s",
            tool.name, layer, elapsed_ms, str(error)[:100],
        )
        return None  # let BIAgentPlugin.on_tool_error handle recovery
