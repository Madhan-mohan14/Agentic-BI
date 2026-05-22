# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity
Agentic Business Intelligence System — PROJECT 3 on Gemini Enterprise Agent Platform (Google Cloud Next '26).
4 pillars: Build · Scale · Govern · Optimize.
Goal: Production-grade portfolio for Agentic AI / Voice AI engineer roles.
Current phase: Days 1–4 complete. Remaining: Dockerfiles + Cloud Run deploy (Day 5).

## Build & Run Commands

```bash
# Install all dependencies
pip install -r requirements.txt

# Run FastMCP server (stdio — for MCP Inspector)
python tools/bi_tools_server.py

# Run FastMCP server (streamable-http — for agent use, port 8088, endpoint /mcp)
MCP_TRANSPORT=http MCP_PORT=8088 python tools/bi_tools_server.py

# Test with MCP Inspector (requires Node.js)
npx @modelcontextprotocol/inspector python tools/bi_tools_server.py

# ADK web UI — start MCP server first in a separate terminal, then run:
adk web

# ADK web UI with Vertex AI Sessions (persistent across browser restarts)
adk web --session_service_uri="agentengine://<AGENT_ENGINE_ID>"

# Run evals
adk eval eval/bi_evalset.test.json --config eval/eval_config.json

# Deploy to Cloud Run (Day 5)
gcloud run deploy orchestrator-service --source . --region us-central1
```

## Agent Architecture

```
root_agent (agents/agent.py · LlmAgent · gemini-2.5-flash · Agent Engine)
├── rag_agent       — FIRST: search_knowledge_base before any BigQuery call
├── analysis_agent  — generate_kpi_summary + detect_anomaly via FastMCP (SSE)
├── data_agent      — execute_sql + get_schema_context via FastMCP (SSE); also owns MCP server lifecycle
├── research_agent  — google_search (ADK built-in)
└── audit_agent     — LlmAgent with Python function tools: score_answer, log_to_corpus, escalate_hitl
```

Query flow: User NL → orchestrator → rag_agent (cache check) → specialist agent →
audit_agent (score ≥ 0.8: log + approve; < 0.8: regenerate or HITL) → User

**MCP Server lifecycle**: `data_agent.py` auto-starts `tools/bi_tools_server.py` as a subprocess (SSE on port 8088) when `MCP_SSE_URL` points to localhost. All agents that use MCP tools connect to the same server. In production, set `MCP_SSE_URL` to the Cloud Run URL.

**Audit Agent**: Implemented as an `LlmAgent` sub-agent (NOT a separate FastAPI service). HITL escalations write to `hitl_queue.json`. The `score_answer` function calls Gemini directly for methodology review.

## File Structure

```
agents/agent.py          # root_agent (orchestrator) — sub_agents list lives here
agents/rag_agent.py      # search_knowledge_base via RAG Engine; session-level cache via output_key
agents/data_agent.py     # execute_sql + schema-first enforced; owns MCP server subprocess
agents/analysis_agent.py # generate_kpi_summary + detect_anomaly via FastMCP
agents/research_agent.py # google_search (ADK built-in, no MCP setup needed)
agents/audit_agent.py    # LlmAgent + Python tool functions: score_answer, log_to_corpus, escalate_hitl
tools/bi_tools_server.py # FastMCP server — all 7 tools; SSE transport on port 8088
tools/callbacks.py       # before/after_tool_callback (shared across agents)
tools/plugin.py          # BIAgentPlugin: on_tool_error + on_model_error (graceful degradation)
tools/observability.py   # setup_tracing() — OTel → Langfuse; call once at startup
tools/tools.yaml         # MCP Toolbox Layer 2 config (3 BigQuery SQL tools for toolbox.exe)
eval/bi_evalset.test.json
eval/eval_config.json    # criteria: tool_trajectory_avg_score≥0.8, hallucinations_v1
eval/scenarios/          # novice.json, expert.json, evaluator.json persona test cases
eval/optimize.py         # weekly: pull traces score<0.7, A/B prompt patches
deployment/policy.yaml   # Agent Gateway runtime policy
deployment/deploy_notes.md      # session backend options (SQLite / Vertex AI / PostgreSQL)
deployment/setup_agent_engine.py # creates Reasoning Engine; prints AGENT_ENGINE_ID
hitl_queue.json          # HITL escalation store (written by audit_agent.escalate_hitl)
```

## MCP Layer — All 3 Types

### Layer 1 — FastMCP (`tools/bi_tools_server.py`) — 7 tools
| Tool | Args | Purpose |
|------|------|---------|
| `execute_sql` | sql, description | SELECT-only query on thelook_ecommerce; enforces LIMIT |
| `detect_anomaly` | table, column, threshold | Z-score outlier flagging; column allowlist prevents injection |
| `generate_kpi_summary` | metrics, days | Revenue/orders/AOV/items_sold vs. prior period |
| `create_report_job` | query_id, format | Queues async export; returns job_id + ETA |
| `get_schema_context` | dataset | Table/column descriptions; must be called before `execute_sql` |
| `log_resolution` | query, result, score | Uploads approved answer to Vertex AI RAG Engine corpus |
| `search_knowledge_base` | query | Semantic retrieval from RAG corpus; top_k=3 |

### Layer 2 — MCP Toolbox (`tools/tools.yaml` + `toolbox.exe`)
`get_top_products` · `get_revenue_by_period` · `get_inventory_anomalies`
Run `toolbox.exe` locally on port 5000. Set `TOOLBOX_URL=http://127.0.0.1:5000`.

### Layer 3 — Prebuilt (zero setup)
`google_search` — ADK built-in used directly by `research_agent` (no MCP config needed).

## Data Sources

### Priority: `bigquery-public-data.thelook_ecommerce`
| Table | Primary Use |
|-------|-------------|
| `orders` | Revenue, order counts, AOV, week-over-week KPIs |
| `order_items` | Product-level revenue, category breakdowns |
| `inventory_items` | Stock anomalies, supply chain |
| `users` | Customer segmentation, cohort analysis |
| `products` | Category/brand enrichment |

### Secondary: `bigquery-public-data.ga4_obfuscated_sample_ecommerce`
Funnel analysis, traffic sources, behavioral drop-off.

### RAG Store: Vertex AI RAG Engine (`RagManagedDb`)
Corpus ID stored in `RAG_CORPUS_ID` env var. `log_resolution` uploads approved answers;
`search_knowledge_base` retrieves them. Skips gracefully if `RAG_CORPUS_ID` is not set.

## Coding Rules
- All ADK agent methods must be `async/await`
- Every FastMCP tool must have a matching trajectory case in `bi_evalset.test.json`
- Never hardcode project IDs or API keys — always `os.environ.get("KEY")`
- `data_agent` always calls `get_schema_context` before `execute_sql` — enforced by `before_tool_callback`
- `execute_sql` only allows SELECT; auto-appends LIMIT 100 if missing
- `detect_anomaly` silently corrects invalid `table` arg to `"orders"` in `before_tool_callback`
- Audit: score ≥ 0.8 → approve + `log_to_corpus()`; score < 0.8 → `escalate_hitl()`; ×2 rejections → HITL
- All agents use `gemini-2.5-flash`; eval config also scores with `gemini-2.5-flash`
- `setup_tracing()` is called at module level in agent files — it's a no-op if Langfuse keys are absent

## Wrong Tool Defense — 6 Layers
1. `before_tool_callback` — blocks `execute_sql` until schema checked; corrects bad table/dataset args; blocks low-score `log_resolution`
2. `after_tool_callback` — injects `retry_hint` on empty SQL results; flags high-threshold anomaly misses; flags RAG cache miss
3. `BIAgentPlugin.on_tool_error` — BigQuery timeout → cached fallback; Connection refused → MCP server hint; quota → retry_after
4. `BIAgentPlugin.on_model_error` — Gemini quota/overload → user-facing retry message
5. Pydantic return models — each tool returns a typed model; malformed responses caught at schema boundary
6. `adk eval` trajectory — wrong tool call order caught pre-deployment

## Environment Variables
| Variable | Purpose |
|----------|---------|
| `GOOGLE_CLOUD_PROJECT` | GCP project for BigQuery + Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | Region (default `us-central1`) |
| `GOOGLE_API_KEY` | Gemini API key (Google AI Studio) |
| `RAG_CORPUS_ID` | Vertex AI RAG corpus resource name |
| `MCP_SSE_URL` | FastMCP server URL; defaults to `http://127.0.0.1:8088/sse` |
| `MCP_PORT` | Port for SSE server (default `8088`) |
| `TOOLBOX_URL` | MCP Toolbox endpoint (default `http://127.0.0.1:5000`) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | OTel tracing (optional) |
| `LANGFUSE_HOST` | Langfuse instance URL |
| `AGENT_ENGINE_ID` | Vertex AI Reasoning Engine ID for cloud sessions |
| `HITL_STORE` | Path to HITL queue JSON (default `hitl_queue.json`) |

## Eval Pipeline
| Layer | Tool | When |
|-------|------|------|
| Trajectory | `adk eval` + `.test.json` | Every PR, pre-deploy |
| User simulation | ADK `ConversationScenario` (3 personas) | Pre-deploy |
| Hallucination | `score_answer` in audit_agent (Gemini) | Every query, production |
| Dev observability | OTel → Langfuse | Development |
| Prompt optimization | `optimize.py` weekly | Pull traces <0.7, A/B patch |

## Memory Bank + Self-Improving Loop
```
rag_agent: search_knowledge_base → cache hit → skip BigQuery → audit → log_resolution → corpus
                                 → cache miss → specialist agent → audit → log_resolution → corpus
audit_agent: score ≥ 0.8 → log_to_corpus() → corpus grows
             score < 0.8 → escalate_hitl() → rejection_count++
             rejection_count ≥ 2 → hitl_queue.json (awaits human correction)
```

## Deployment Targets (Day 5)
- **Cloud Run**: `orchestrator-service` (source: repo root) + `audit-agent-service`
- **Agent Engine**: deploy orchestrator via `adk deploy`; use `setup_agent_engine.py` to create the engine
- **Session backends**: SQLite (default, local), Vertex AI Sessions (`agentengine://`), PostgreSQL
- **Agent Gateway**: route all MCP + A2A traffic via `deployment/policy.yaml`

## .claude/ Skills
| Skill | Purpose |
|-------|---------|
| `skills/adk-agent` | ADK patterns: LlmAgent, sub_agents, callbacks, A2A wiring |
| `skills/teaching-mode` | Post-build: explain what was built + why (learning mode) |
| `skills/mcp-builder` | FastMCP tool patterns, MCP Inspector testing workflow |
| `skills/mistake-tracker` | Log mistakes immediately; check before starting any task |

## Mistake Log
(Entries added here as mistakes occur — never repeat)
| Date | Mistake | Fix | Avoid When |
|------|---------|-----|------------|
| — | — | — | — |

## Reference Links
| Resource | URL |
|----------|-----|
| ADK Docs | https://google.github.io/adk-docs |
| ADK Eval Guide | https://google.github.io/adk-docs/evaluate |
| ADK Callbacks | https://google.github.io/adk-docs/callbacks |
| ADK MCP Tools | https://google.github.io/adk-docs/mcp-tools |
| ADK A2A Protocol | https://google.github.io/adk-docs/a2a |
| FastMCP Docs | https://github.com/jlowin/fastmcp |
| MCP Inspector | https://github.com/modelcontextprotocol/inspector |
| MCP Toolbox (BigQuery) | https://github.com/googleapis/mcp-toolbox-for-databases |
| RAG Engine Overview | https://cloud.google.com/vertex-ai/generative-ai/docs/rag-overview |
| corroborateContent API | https://cloud.google.com/vertex-ai/generative-ai/docs/grounding/corroborate-content |
| BigQuery Public Datasets | https://cloud.google.com/bigquery/public-data |
| TheLook Ecommerce | https://console.cloud.google.com/marketplace/details/bigquery-public-data/thelook-ecommerce |
| OTel ADK Instrumentation | https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-google-adk |
| Langfuse | https://langfuse.com |
| Agent Garden Samples | https://github.com/google/adk-samples |
