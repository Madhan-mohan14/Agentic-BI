---
name: mcp-builder
description: >
  Guides building, testing, and running the FastMCP server for this project (tools/bi_tools_server.py).
  Use this skill whenever writing or modifying any of the 6 FastMCP tools, adding Pydantic return
  models, running MCP Inspector, debugging tool schema issues, or wiring FastMCP tools into ADK agents.
  If the user mentions MCP, FastMCP, bi_tools_server, MCP Inspector, detect_anomaly, generate_kpi_summary,
  create_report_job, get_schema_context, log_resolution, or search_knowledge_base — use this skill.
  Don't try to guess FastMCP patterns; use this skill for the exact patterns that work.
---

# MCP Server Builder

This project's Layer 1 MCP is a single file: `tools/bi_tools_server.py` with 6 tools. All 6 tools live in one file. The MCP Inspector is the primary way to verify they work before wiring into ADK agents.

## FastMCP Tool Pattern
Every tool follows this exact structure — type hints required, Pydantic return model required, async required, docstring required.

```python
from fastmcp import FastMCP
from pydantic import BaseModel
import os

mcp = FastMCP("bi-tools-server")

# 1. Define return model first (FastMCP reads the schema from this)
class AnomalyResult(BaseModel):
    is_anomaly: bool
    score: float
    affected_rows: int
    details: str

# 2. Decorate with @mcp.tool() — this registers it and generates the JSON schema
@mcp.tool()
async def detect_anomaly(table: str, column: str, threshold: float = 2.0) -> AnomalyResult:
    """
    Flags statistical outliers in a business data column using z-score.
    Use for revenue drops, order volume spikes, or any numeric KPI anomalies.
    """
    # Implementation: compute z-score, compare to threshold
    return AnomalyResult(
        is_anomaly=False,
        score=0.0,
        affected_rows=0,
        details="within normal range"
    )

# 3. Entrypoint — required to run as MCP server
if __name__ == "__main__":
    mcp.run()  # defaults to stdio transport — works with MCP Inspector
```

Why type hints matter: FastMCP auto-generates the JSON schema for the tool from Python type hints. If a type hint is missing, the agent calling this tool won't know what args to send. No manual schema writing needed — the hints do it.

Why Pydantic return model matters: Raw dicts don't generate output schemas. Pydantic models give the calling agent a structured, predictable response it can reason about.

## All 6 Tools — Implementation Targets

```python
# Tool 1 — Statistical anomaly detection
@mcp.tool()
async def detect_anomaly(table: str, column: str, threshold: float = 2.0) -> AnomalyResult:
    """Flags z-score outliers in revenue, order volume, or any numeric column."""

# Tool 2 — KPI summary generation  
@mcp.tool()
async def generate_kpi_summary(metrics: list[str], period: str) -> KpiSummary:
    """Rolls up business KPIs (revenue, orders, conversion) into a plain-English summary."""

# Tool 3 — Async report job queue
@mcp.tool()
async def create_report_job(query_id: str, format: str = "pdf") -> ReportJob:
    """Queues an async report generation job. Returns job_id and estimated completion time."""

# Tool 4 — Schema context for agents
@mcp.tool()
async def get_schema_context(dataset: str) -> SchemaContext:
    """Returns human-readable table descriptions so agents understand the data before querying."""

# Tool 5 — Write approved analysis to RAG corpus
@mcp.tool()
async def log_resolution(query: str, result: str, score: float) -> LogResult:
    """Writes an approved analysis (score >= 0.8) into the RAG Engine corpus for future retrieval."""

# Tool 6 — Semantic search over past analyses
@mcp.tool()
async def search_knowledge_base(query: str) -> KBSearchResult:
    """Searches RAG Engine for past analyses similar to the current query. Called by RAG Agent first."""
```

## Running & Testing

```bash
# Start the MCP server (stdio transport)
python tools/bi_tools_server.py

# Launch MCP Inspector (requires Node.js — installs automatically)
npx @modelcontextprotocol/inspector python tools/bi_tools_server.py
# → Opens at http://localhost:5173
# → Connect → pick a tool → fill JSON args → click Call → verify response

# Production: HTTP transport (for ADK agent wiring)
# In bi_tools_server.py, change the entrypoint:
# mcp.run(transport="http", host="0.0.0.0", port=8000)
```

Start the server FIRST, then launch Inspector — connecting before the server is ready causes "connection refused".

## Wiring into ADK Agent
Once tools are verified in MCP Inspector, wire into an ADK agent like this:

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters

analysis_agent = LlmAgent(
    name="analysis_agent",
    model="gemini-2.0-flash",
    tools=[
        MCPToolset(
            connection_params=StdioServerParameters(
                command="python",
                args=["tools/bi_tools_server.py"]
            )
        )
    ]
)
```

## 6 Tools Completion Checklist
- [ ] detect_anomaly — AnomalyResult(is_anomaly, score, affected_rows, details)
- [ ] generate_kpi_summary — KpiSummary(summary_text, top_metric, period, change_pct)
- [ ] create_report_job — ReportJob(job_id, format, eta_seconds, status)
- [ ] get_schema_context — SchemaContext(dataset, tables: list[TableInfo])
- [ ] log_resolution — LogResult(logged, corpus_id, chunk_id)
- [ ] search_knowledge_base — KBSearchResult(results: list[PastAnalysis], count)

Each tool also needs a matching test case in `eval/bi_evalset.test.json` before it's considered done.
