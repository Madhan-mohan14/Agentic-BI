---
name: adk-agent
description: >
  Guides building, wiring, and debugging Google ADK agents for the Agentic BI project.
  Use this skill whenever writing or editing any agent file — orchestrator.py, rag_agent.py,
  data_agent.py, analysis_agent.py, research_agent.py, or audit_agent.py. Also trigger for
  any question about LlmAgent construction, sub_agents routing, callbacks, plugins, A2A wiring,
  session state, or Gemini model selection. If the user mentions ADK, sub-agent, callback,
  or plugin in this project context, use this skill — don't try to recall patterns from memory.
---

# ADK Agent Builder

This project has 6 agents across two deployment targets. Every agent decision maps to one of the 4 platform pillars (Build / Scale / Govern / Optimize). Use the patterns here exactly — they encode what works for this specific stack.

## Agent Roster Quick Reference
| Agent | Type | Model | File |
|-------|------|-------|------|
| Orchestrator | LlmAgent (root) | gemini-2.0-flash | agents/orchestrator.py |
| RAG Agent | LlmAgent (sub) | gemini-2.0-flash | agents/rag_agent.py |
| Data Agent | LlmAgent (sub) | gemini-2.0-flash | agents/data_agent.py |
| Analysis Agent | LlmAgent (sub) | gemini-2.0-flash | agents/analysis_agent.py |
| Research Agent | LlmAgent (sub) | gemini-2.0-flash | agents/research_agent.py |
| Audit Agent | FastAPI + A2A | gemini-2.5-flash | agents/audit_agent.py |

Gemini 2.0 Flash = all agent work. Gemini 2.5 Flash = eval/audit scoring ONLY.

## LlmAgent Construction
```python
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

agent = LlmAgent(
    name="orchestrator",
    model="gemini-2.0-flash",
    instruction="""You are the orchestrator...""",
    tools=[FunctionTool(my_tool_fn)],
    sub_agents=[rag_agent, data_agent, analysis_agent, research_agent],
    output_schema=MyPydanticModel,   # enforces structured response
    before_agent_callback=before_agent_cb,
    before_tool_callback=before_tool_cb,
    after_tool_callback=after_tool_cb,
)
```

Routing rule: RAG Agent is always called first. Never route directly to Data Agent cold — always check the knowledge base first to avoid unnecessary BigQuery costs.

## Callbacks — Logic Defense Layer
Callbacks intercept tool calls before/after execution. They handle wrong logic (wrong args, wrong order, empty results). They are NOT for crash handling — that's Plugin's job.

```python
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.base_tool import BaseTool
from typing import Optional

def before_tool_callback(
    tool: BaseTool, args: dict, ctx: CallbackContext
) -> Optional[dict]:
    # Return a dict to OVERRIDE the call (block it), return None to ALLOW it
    if tool.name == "bigquery_execute_sql":
        if "dataset" not in args:
            return {"error": "dataset required — call bigquery_get_schema first"}
    return None  # allow everything else

def after_tool_callback(
    tool: BaseTool, args: dict, ctx: CallbackContext, result: any
) -> Optional[any]:
    # Return modified result to OVERRIDE, return None to KEEP as-is
    if not result or result == {}:
        return {"error": "empty result", "retry_hint": "check schema and requery"}
    return None
```

## BIAgentPlugin — Crash Defense Layer
Plugin handles runtime crashes: network timeouts, quota errors, connection refused. This is separate from callbacks because crashes happen outside the normal tool call lifecycle.

```python
from google.adk.agents import BasePlugin

class BIAgentPlugin(BasePlugin):
    def on_tool_error(self, tool_name: str, error: Exception, ctx):
        if "timeout" in str(error).lower() or "deadline" in str(error).lower():
            cached = ctx.session.state.get("last_bq_result")
            if cached:
                return {"cached_result": cached, "warning": "BigQuery timeout — using cached"}
            return {"error": "BigQuery unavailable", "suggestion": "retry in 30s"}

    def on_model_error(self, model: str, error: Exception, ctx):
        if "quota" in str(error).lower() or "429" in str(error):
            return {"fallback_model": "gemini-2.0-flash", "delayed": True}
        return None
```

## Session State — Memory Within a Session
Session state persists data across tool calls within one conversation. Use it to pass context between agents without re-querying.

```python
# Write to session state (inside any agent or callback)
ctx.session.state["last_bq_result"] = query_result
ctx.session.state["rag_context"] = similar_analyses
ctx.session.state["rejection_count"] = ctx.session.state.get("rejection_count", 0) + 1

# Read from session state
rejection_count = ctx.session.state.get("rejection_count", 0)
if rejection_count >= 2:
    # Trigger HITL
    pass
```

## A2A — Audit Agent as External Service
The Audit Agent is a separate FastAPI service with its own Cloud Run URL. It is NOT a sub_agent — it's called via HTTP A2A protocol.

```python
# agents/audit_agent.py — FastAPI A2A service
from fastapi import FastAPI
from google.adk.a2a import AgentExecutor

app = FastAPI()

# Agent Card — required for A2A discovery
@app.get("/.well-known/agent.json")
async def agent_card():
    return {
        "name": "audit-agent",
        "description": "Actor-Critic hallucination scoring — hallucinations_v1 + corroborateContent",
        "url": os.environ["AUDIT_AGENT_URL"],
        "capabilities": ["hallucination_scoring", "corroborate_content"],
        "version": "1.0.0"
    }

# Task handler
@app.post("/tasks")
async def handle_task(request: dict):
    analysis_text = request["input"]["analysis"]
    rag_corpus = request["input"]["corpus_id"]
    score = await run_hallucination_check(analysis_text, rag_corpus)
    return {
        "score": score,
        "verdict": "APPROVED" if score >= 0.8 else "REJECTED",
        "threshold": 0.8
    }
```

## Key Rules
- All agent methods must be `async/await` — synchronous methods silently fail in ADK
- `sub_agents` = internal to orchestrator, same process; A2A = external HTTP service
- `before_tool_callback` → fires before the tool runs; `after_tool_callback` → fires after
- Plugin handles exceptions; callbacks handle wrong-but-valid calls
- RAG Agent always routes first — BigQuery is never the first call
- Session state stores within-session context; Firestore Memory Bank stores cross-session
