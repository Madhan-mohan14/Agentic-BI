# Interview Preparation — Agentic Business Intelligence System

---

## 1. 30-Second Pitch

I built a production-grade multi-agent BI system on Google's Gemini Enterprise Agent Platform. Five specialized agents — each with a single responsibility — collaborate via the A2A protocol: a RAG cache agent checks approved answers first, specialist agents run BigQuery analytics, and a dedicated audit agent scores every answer using Gemini as a judge before it reaches the user. Any approved answer is automatically written back to the Vertex AI RAG corpus, so the same question never hits BigQuery twice. The whole system is deployed: orchestrator on Vertex AI Agent Engine, MCP server and audit agent on Cloud Run, with Terraform-managed infrastructure. It implements all three MCP patterns simultaneously — custom FastMCP tools, prebuilt MCP Toolbox for Databases, and ADK's built-in Google Search. The result is a self-improving BI assistant that gets cheaper and faster with every query, with a quality gate that catches hallucinations before users see them.

---

## 2. STAR Method Story

**Situation:** I was building an agentic BI system and hit a subtle production bug: users were asking questions like "what are the KPIs for the last 6 days" and the system kept calling BigQuery every single time — even after the answer had been approved and supposedly saved to the RAG corpus. The RAG cache appeared to be completely broken in production despite working fine in unit tests.

**Task:** Diagnose why the RAG cache always missed, even when corpus queries returned 3 results, and fix it without breaking the multi-agent pipeline.

**Action:** I traced the full execution path: `search_knowledge_base` → `after_tool_callback` → `rag_agent` output. The `after_tool_callback` in `tools/callbacks.py` (lines 117–139) enforces a 24-hour TTL by scanning each result for a `"Logged: YYYY-MM-DD"` timestamp. If no result has today's date, it overrides the count to 0 and forces a cache miss. I found the `log_resolution` tool in `tools/bi_tools_server.py` correctly prepended that stamp. But `log_to_corpus` in `audit_agent/agent.py` — the function actually called in production — did not. It was writing corpus entries without the timestamp, so every entry appeared stale on retrieval. I added `import datetime as _dt` and changed line 104 to `f"Logged: {_dt.date.today().isoformat()}\nQuery: {query}\n..."`, redeployed `audit-agent-service` to Cloud Run, and verified with a direct RAG query showing `age=0d → FRESH`. The cache hit rate went from 0% to working correctly.

**Result:** The self-improving loop is now fully operational. Every approved answer (score ≥ 0.8) enters the corpus with a fresh timestamp; the TTL check passes; subsequent identical questions skip BigQuery entirely. I also discovered a second root cause that had been hiding the first: a prior `gcloud run deploy bi-tools-server` without `--allow-unauthenticated` had removed the `allUsers` invoker IAM binding, causing `McpToolset` (which makes plain HTTP requests with no Google identity token) to receive 401 errors — `search_knowledge_base` silently returned count=0, masking the TTL bug. I documented both failure modes in CLAUDE.md and in the memory system to prevent recurrence.

---

## 3. Architecture Diagram

### Request Flow

```
User NL Query
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR  (Vertex AI Agent Engine · gemini-2.5-flash)      │
│  orchestrator/agent.py                                           │
│  ┌── _safety_model_filter (before_model_callback)               │
│  │   regex blocks: injection patterns, hard-blocked terms        │
│  └── _before_orchestrator (before_agent_callback)               │
│      injects session_id into state for analytics                 │
└──────┬──────────────────────────────────────────────────────────┘
       │  STEP 1 (always)
       ▼
┌──────────────────────────────────────────────────┐
│  RAG AGENT  (sub_agents/rag_agent/agent.py)       │
│  McpToolset → search_knowledge_base (FastMCP)     │
│  after_tool_callback: 24hr TTL check              │
└──────┬───────────────────────────────────────────┘
       │ CACHE HIT?                    │ CACHE MISS (ANSWER NOT FOUND)
       │ return is_cached=True         ▼
       │                   STEP 2 (one specialist)
       │          ┌────────────────────────────────┐
       │          │  analysis_agent  data_agent     │
       │          │  research_agent                 │
       │          │  (FastMCP Layer 1 / Toolbox L2  │
       │          │   / google_search L3)            │
       │          └───────────────┬────────────────┘
       │                          │
       └──────────────────────────┘
                         │ STEP 3 (always)
                         ▼
┌──────────────────────────────────────────────────┐
│  AUDIT AGENT  (Cloud Run · A2A microservice)      │
│  audit_agent/agent.py → to_a2a()                  │
│  score_answer (Gemini judge · temperature=0.0)    │
│  ┌─ score ≥ 0.8 → log_to_corpus → RAG corpus grows│
│  └─ score < 0.8 → escalate_hitl → Firestore queue │
└──────────────────────────────────────────────────┘
                         │
                         ▼
                  Answer to User
```

### Infrastructure

```
Vertex AI Agent Engine ─── orchestrator (managed, auto-scaling)
Cloud Run ─────────────┬── bi-tools-server (FastMCP · port 8080)
                       └── audit-agent-service (A2A · uvicorn)
Vertex AI RAG Engine ──── corpus (RagManagedDb · semantic retrieval)
BigQuery ──────────────┬── bigquery-public-data.thelook_ecommerce
                       └── agent_analytics.tool_events (plugin writes)
Firestore ─────────────── hitl_queue (HITL escalation docs)
Terraform ─────────────── deployment/terraform/single-project/ (IAM, BQ, Cloud Run)
```

---

## 4. Interview Q&A

### Architecture

**Q: Why did you use five separate agents instead of one agent with all the tools?**

A: Single-agent systems with many tools produce two failure modes I wanted to avoid: tool confusion (the model picks the wrong tool for a question type) and quality bypass (no clean place to intercept and score the answer). By isolating `rag_agent` as the always-first cache check, I enforce a strict order: cache before compute. `data_agent` and `analysis_agent` are separate because their tool sets and failure modes differ — SQL schema enforcement vs. KPI math. The audit agent has to be separate because it needs to be independently scalable and replaceable; if I swap the LLM-as-judge for a human reviewer, only the audit agent changes. The orchestrator's 3-step instruction in `orchestrator/agent.py` (lines 165–186) makes the routing deterministic.

**Q: Why A2A for the audit agent specifically?**

A: Two reasons. First, the audit agent needs to be independently deployable — I can update the scoring prompt, the Gemini config, or the HITL threshold without redeploying the orchestrator or any other agent. Second, A2A gives me a proper service boundary: the agent card at `/.well-known/agent.json` (auto-generated by `to_a2a()`) describes the agent's capabilities in a machine-readable format. Any A2A-compatible consumer can plug in without code changes. The `_load_audit_card()` function in `orchestrator/agent.py` (line 45) fetches and patches the card URL because `to_a2a()` defaults to `localhost:8080` — that's the kind of detail that only surfaces in production.

**Q: Why Vertex AI RAG Engine instead of a vector database like Pinecone or pgvector?**

A: Three reasons. (1) It's managed — no indexing infrastructure to maintain. (2) It's tightly integrated with the Vertex AI stack: `rag.upload_file()` and `rag.retrieval_query()` use the same auth as every other service. (3) `RagManagedDb` handles chunking and embedding automatically. The tradeoff is less control over the retrieval pipeline — I can't tune the embedding model or the similarity metric. For a BI assistant where the corpus is structured, short, and date-stamped, that tradeoff is fine. If I needed metadata filtering (e.g., "only retrieve answers from the last 30 days by SQL"), I'd move to Weaviate or Vertex AI Matching Engine.

**Q: What happens when the orchestrator's `before_model_callback` fires and blocks a request?**

A: `_safety_model_filter` in `orchestrator/agent.py` (line 95) is registered as `before_model_callback` — it runs before every LLM API call, scanning all user-role text in `llm_request.contents`. If it matches `_BLOCK_PATTERNS` (SQL injection, shell commands, etc.) or `_INJECTION_PATTERNS` (jailbreak phrases), it returns an `LlmResponse` directly, short-circuiting the model call entirely. The user gets a polite refusal; Gemini is never called. This is the Model Armor substitute — I built it because Model Armor wasn't available in the project tier, but the pattern is identical: intercept at the model boundary, not at the agent boundary.

**Q: How does the orchestrator know which specialist to call?**

A: The routing instruction in `orchestrator/agent.py` (lines 170–174) is explicit: customer rankings, top-N, breakdowns → `data_agent`; KPIs, revenue totals, anomalies → `analysis_agent`; why something happened, external context → `research_agent`. The LLM reads the user's question and matches it to those categories. The backup defense is the eval trajectory in `eval/bi_evalset.test.json` — every routing decision has a golden case. Wrong routing is caught pre-deploy, not in production.

### MCP & Tools

**Q: You have three MCP layers. Why not just use one?**

A: They solve different problems. FastMCP (Layer 1) is for custom tools I had to build: `detect_anomaly`, `generate_kpi_summary`, `log_resolution` don't exist anywhere else. MCP Toolbox (Layer 2) gave me three BigQuery-native tools (`get_top_products`, `get_revenue_by_period`, `get_inventory_anomalies`) with zero SQL boilerplate — the toolbox handles query templates and parameterization. ADK's built-in `google_search` (Layer 3) is zero-configuration: no server, no transport, just `research_agent` listing it in `tools=`. Using all three demonstrates that I understand when to build vs. when to reach for an existing tool.

**Q: Why FastMCP over the official MCP Python SDK?**

A: FastMCP has a decorator-based API (`@mcp.tool()`) that lets me define a tool as a Python function with type annotations — identical to how I'd write any function. The official SDK requires manual `Tool` object construction and handler registration. For 7 tools, FastMCP is significantly less boilerplate. It also handles transport negotiation (stdio vs. streamable-HTTP) via environment variables, which let me use the same server file for both local MCP Inspector testing and Cloud Run production without code changes.

**Q: What does `before_tool_callback` actually enforce?**

A: Three rules, all in `tools/callbacks.py` (lines 14–57). (1) `detect_anomaly` silently corrects invalid `table` arguments to `"orders"` — the agent sometimes passes table names from context that don't exist in the allowlist. (2) `get_schema_context` strictly validates the `dataset` argument and blocks the call with an error if it's unknown — this is the schema-first enforcement that prevents `execute_sql` from running without context. (3) `log_resolution` is blocked if `score < 0.8` — no low-quality answer can pollute the corpus, regardless of what the agent decides.

### Production & Reliability

**Q: How do you handle BigQuery timeouts?**

A: `BIAgentPlugin.on_tool_error` in `tools/plugin.py` (lines 68–81) catches any `Timeout` or `DeadlineExceeded` exception. If there's a cached result in session state (`tool_context.state["last_bq_result"]`), it returns that with a warning. If not, it returns a structured error with an actionable message: "Try a narrower date range." The cache is written by `BIAgentPlugin.after_tool_call` (lines 147–160) after every successful `execute_sql` call. This means the first successful query within a session becomes a fallback for subsequent timeouts in the same session.

**Q: You mentioned the RAG cache was always missing. How did you debug that?**

A: I traced the data path layer by layer. First, I confirmed the corpus had entries by calling `rag.retrieval_query()` directly — it returned 3 results, so the corpus wasn't empty. Then I checked the count parsing in `after_tool_callback`: the McpToolset wraps tool responses as `{'content': [{'type': 'text', 'text': '<json>'}]}` (lines 101–107 in callbacks.py), so I had to parse the nested JSON to get the actual count. Count was 3, not 0 — so the callback was getting past the count check. Then I read the TTL check (lines 117–139): it scans for `"Logged: YYYY-MM-DD"`. I printed a raw corpus entry and saw no such prefix. I grep'd for `"Logged:"` in the codebase and found it in `bi_tools_server.py` (log_resolution) but not in `audit_agent/agent.py` (log_to_corpus). That was the root cause: two write paths, only one with the stamp.

**Q: What's in your HITL queue and when does it trigger?**

A: `escalate_hitl` in `audit_agent/agent.py` (lines 142–166) tracks a per-session-per-query rejection count using `hashlib.md5(f"{session_id}:{query}")` as the key. First rejection: `action=regenerate` — the orchestrator calls the same specialist once more. Second rejection: `action=hitl` — writes a Firestore document to the `hitl_queue` collection with the query, result, score, and session ID. A human reviewer reads the queue, corrects the answer, and uploads it to the corpus manually. The two-rejection threshold avoids sending every borderline answer to humans — only answers the model consistently fails to improve.

### Weaknesses (ask them before they do)

**Q: What are the weaknesses of this system?**

A: Three honest ones. First, the routing is prompt-based, not deterministic. If the user asks a question that spans multiple categories ("which customers have the highest revenue AND any anomalies in their order history?"), the orchestrator might pick only one specialist. I mitigate this with explicit routing rules in the instruction, but a workflow-based router would be more reliable. Second, the 24-hour TTL means a heavily-trafficked question gets re-computed once per day even if the underlying data hasn't changed. A smarter TTL would check BigQuery's table modification timestamp before expiring the cache. Third, the audit score from Gemini is an approximation — `score_answer` uses `temperature=0.0` to reduce variance, but it's still a language model judging another language model's output. For true production use, I'd want a golden test set with human-labeled correct answers to calibrate the judge's scoring against ground truth.

---

## 5. Concepts Table

| Concept | Definition | Where in Code |
|---------|-----------|---------------|
| LlmAgent | ADK base class for LLM-backed agents; wraps Gemini model, tools, callbacks, instructions | `orchestrator/agent.py:144`, `sub_agents/*/agent.py`, `audit_agent/agent.py:173` |
| AgentTool | Wraps a sub-agent as a callable tool for the orchestrator | `orchestrator/agent.py:149-153` |
| RemoteA2aAgent | Consumes an A2A-exposed agent over HTTP; uses agent card for discovery | `orchestrator/agent.py:78-82` |
| to_a2a() | ADK utility that wraps an LlmAgent as an ASGI A2A server | `audit_agent/agent.py:216` |
| McpToolset | ADK MCP client; connects to MCP server, discovers and calls tools | `sub_agents/rag_agent/agent.py:46-54` |
| StreamableHTTPConnectionParams | Transport config for MCP streamable-HTTP; sets timeout, SSE read timeout | `sub_agents/rag_agent/agent.py:47-51` |
| before_tool_callback | Runs before every tool call; can block, correct args, or pass through | `tools/callbacks.py:14-57` |
| after_tool_callback | Runs after every tool call; can override the tool response | `tools/callbacks.py:60-149` |
| BasePlugin | ADK plugin base; `on_tool_error`, `on_model_error`, `before/after_tool_call` hooks | `tools/plugin.py:40`, `tools/plugin.py:162` |
| output_key | Agent state key where the agent's last response is written | `sub_agents/rag_agent/agent.py:61` |
| before_model_callback | Runs before every LLM API call; can short-circuit the model | `orchestrator/agent.py:95-140, 206` |
| 24hr TTL | Cache freshness check: scans corpus results for `Logged: YYYY-MM-DD` | `tools/callbacks.py:117-139` |
| RAG corpus | Vertex AI `RagManagedDb`; stores approved BI answers for retrieval | `audit_agent/agent.py:85-139`, `tools/bi_tools_server.py` |
| A2A protocol | Agent-to-Agent HTTP protocol; agent card at `/.well-known/agent.json` | `orchestrator/agent.py:45-82` |
| HITL queue | Firestore `hitl_queue` collection; written on 2nd rejection per query | `audit_agent/agent.py:142-166` |
| BigQueryAnalyticsPlugin | ADK plugin; writes every tool call event to `agent_analytics.tool_events` | `tools/plugin.py:162-313` |
| _safety_model_filter | `before_model_callback` that regex-blocks injection and hard-coded abuse | `orchestrator/agent.py:95-140` |
| Z-score anomaly | `(value - mean) / std > threshold` flags outliers; column allowlist prevents injection | `tools/bi_tools_server.py` |
| AdkApp | ADK wrapper for Agent Engine deployment; wraps root_agent | `orchestrator/agent_runtime_app.py` |
| score_answer | Gemini-as-judge call at `temperature=0.0, thinking_budget=0`; returns 0.0–1.0 | `audit_agent/agent.py:48-82` |

---

## 6. Production Gap List

Honest list of what would need to change before this handles real enterprise traffic:

| Gap | Why It Matters | Mitigation Already In Place |
|-----|---------------|----------------------------|
| Routing is prompt-based | Multi-part questions may route to only one specialist | Explicit routing rules in orchestrator instruction; eval catches wrong routing |
| TTL is calendar-day, not data-change-aware | Cache expires even if underlying BigQuery table hasn't changed | 24hr TTL is conservative enough for daily BI use |
| LLM-as-judge score calibration | Gemini scoring Gemini introduces correlated errors | `temperature=0.0`, calibrated prompt with explicit plausible ranges |
| No streaming | Users wait for full response before seeing anything | `max_output_tokens=2048` keeps responses concise |
| Single RAG corpus | No per-user or per-team isolation | `RAG_CORPUS_ID` env var — swap per tenant |
| Firestore HITL queue is append-only | No built-in review UI or resolution tracking | Queue designed for human review; structure is simple enough to build a UI |
| audit_agent is stateless per call | `_rejection_count` dict is in-process memory, lost on Cloud Run cold start | Two-rejection threshold is per-session; cold starts reset is acceptable |
| No canary / shadow traffic | New agent versions go live immediately on Agent Engine | ADK eval trajectory gates deployment; no % rollout |
| `google_search` is unmetered | No rate limiting on `research_agent` calls | Research agent only called for WHY questions per routing rules |
| Toolbox Layer 2 runs locally | `toolbox.exe` on localhost:5000; not deployed | Cloud Run deployment path exists; not prioritized over FastMCP Layer 1 |

---

## 7. Numbers to Remember

| Number | What It Is | Why This Value |
|--------|-----------|----------------|
| **0.8** | Audit score threshold to approve | Empirically: below 0.8 correlates with vague answers (no numbers, wrong time window); above 0.8 means the answer directly addressed the question with plausible data |
| **3** | RAG top_k results | Enough to cover rephrased versions of the same question; more increases latency and the risk of returning unrelated results |
| **0.0** | `score_answer` temperature | The judge needs to be consistent. Two calls on the same input should return the same score. Temperature=0.0 minimizes variance. |
| **0.4** | Orchestrator temperature | Routing decisions need some flexibility for paraphrased questions, but high temperature causes routing drift. 0.4 balances consistency with adaptability. |
| **2.0** | Z-score anomaly threshold | 2.0 standard deviations = ~4.5% false positive rate on a normal distribution. Standard for anomaly detection in business contexts without excessive noise. |
| **30s** | MCP tool timeout | Sufficient for BigQuery queries on the public thelook dataset; prevents indefinite hang on network issues. |
| **60s** | SSE read timeout | `sse_read_timeout` in `StreamableHTTPConnectionParams` — allows for slow-starting streaming responses without dropping the connection. |
| **45s** | log_to_corpus timeout | RAG uploads involve file I/O + HTTP to Vertex AI; 45s allows for large payloads without blocking the audit pipeline indefinitely. |
| **2** | HITL rejection threshold | One rejection triggers regeneration (model might do better). Two rejections means the question is genuinely hard and needs human input. Three would be too patient. |
| **24hr** | Cache TTL | BI metrics update daily. A result from yesterday's calendar day is stale by definition for daily KPI tracking. |
| **7** | FastMCP tools (Layer 1) | The minimum to cover: SQL, anomaly, KPI, async export, schema context, RAG write, RAG read. Each is a distinct capability; none can be collapsed. |
| **5** | Agent count | Orchestrator + rag + analysis + data + research + audit = 6 total, but 5 sub-agents plus orchestrator. Each has one job. |
| **8958181377107296256** | Agent Engine ID | The existing engine — every deploy is an UPDATE, never a new create. |

---

## 8. File-by-File Summary

### Core Agents

**`orchestrator/agent.py`** — The root of the system. Wires all sub-agents as `AgentTool` instances, wires `audit_agent` as `RemoteA2aAgent` (A2A over HTTP). Enforces the 3-step pipeline via instruction (lines 165–186). Two model-level hooks: `_before_orchestrator` injects `session_id` into state, `_safety_model_filter` regex-blocks injection/abuse before any LLM call. Temperature=0.4 for routing flexibility. Registered with `BIAgentPlugin` + `BigQueryAnalyticsPlugin`.

**`sub_agents/rag_agent/agent.py`** — Single tool: `search_knowledge_base` via McpToolset. Instruction: call the tool, if count=0 output exactly "ANSWER NOT FOUND", if count≥1 output "is_cached=True\n<result>". No paraphrasing. `output_key="rag_result"` writes the result to session state for observability. `after_agent_callback` logs FOUND/NOT_FOUND to stderr.

**`sub_agents/analysis_agent/agent.py`** — `generate_kpi_summary` + `detect_anomaly` via McpToolset. Instruction enforces schema-first: call `get_schema_context` before any SQL-adjacent tool. Returns structured metrics with period-over-period comparison.

**`sub_agents/data_agent/agent.py`** — `execute_sql` + `get_schema_context` via McpToolset. Owns the MCP server subprocess: when `MCP_SSE_URL` points to localhost, starts `tools/bi_tools_server.py` as a subprocess. `before_tool_callback` enforces schema check before SQL; `execute_sql` auto-appends LIMIT 100.

**`sub_agents/research_agent/agent.py`** — Uses ADK's built-in `google_search` tool directly — no MCP, no configuration. Called only for external context questions ("why did revenue drop?").

**`audit_agent/agent.py`** — LlmAgent with three Python function tools: `score_answer` (Gemini judge at temperature=0.0), `log_to_corpus` (writes to Vertex AI RAG corpus with `Logged: YYYY-MM-DD` prefix), `escalate_hitl` (tracks rejections via in-process dict, writes to Firestore on 2nd rejection). Exposed as A2A microservice via `to_a2a()`, deployed to Cloud Run. Agent card auto-generated at `/.well-known/agent.json`.

### Tools & Middleware

**`tools/bi_tools_server.py`** — FastMCP server with 7 tools. Served on streamable-HTTP (port 8088 locally, 8080 on Cloud Run). SELECT-only SQL with auto-LIMIT. Z-score anomaly with column allowlist. `log_resolution` writes corpus entries with `Logged:` prefix. `search_knowledge_base` calls `rag.retrieval_query(top_k=3)`.

**`tools/callbacks.py`** — `before_tool_callback`: silently corrects `detect_anomaly` table arg, strictly validates `get_schema_context` dataset, blocks low-score `log_resolution`. `after_tool_callback`: passes SQL through unchanged, adds advisory on high anomaly threshold, enforces 24hr TTL for `search_knowledge_base` by scanning for `Logged: YYYY-MM-DD`.

**`tools/plugin.py`** — Two plugins. `BIAgentPlugin`: handles infrastructure failures (BigQuery timeout → cached fallback, connection refused → hint, quota → retry_after, RAG unavailable → skip). `BigQueryAnalyticsPlugin`: writes every tool call to `agent_analytics.tool_events`; auto-creates dataset and table; resolves MCP/TOOLBOX/BUILTIN/SUB_AGENT layer; keeps `tool_provenance_history` (last 20) in session state.

**`tools/observability.py`** — `setup_tracing()` wires OpenTelemetry to Langfuse using `openinference-instrumentation-google-adk`. Called at module load in each agent file; no-op if Langfuse keys are absent.

**`tools/tools.yaml`** — MCP Toolbox Layer 2 config. Three BigQuery tools with SQL templates. Run `toolbox.exe` on port 5000.

### Eval & Optimization

**`eval/bi_evalset.test.json`** — Golden eval set. Every tool trajectory case: rag_agent → analysis_agent → audit_agent, with expected tool call sequences and response quality rubrics.

**`eval/eval_config.json`** — Three criteria: `rubric_based_tool_use_quality_v1 ≥ 0.7`, `rubric_based_final_response_quality_v1 ≥ 0.7`, `hallucinations_v1 ≥ 0.8`. Eval model: gemini-2.5-flash.

**`eval/optimize.py`** — Weekly script: queries `agent_analytics.tool_events` for sessions with audit score < 0.7, clusters by tool name and args pattern, generates candidate prompt patches for A/B testing.

**`eval/scenarios/`** — Three persona test sets: `novice.json` (plain English questions), `expert.json` (technical BI terms, complex filters), `evaluator.json` (adversarial, edge cases, injection attempts).

### Infrastructure

**`deploy_agent_runtime.py`** — Custom deploy script. Packages `./orchestrator`, `./sub_agents`, `./tools` into one tarball (agents-cli default only packages `./orchestrator`). Updates engine `8958181377107296256` — never creates a new one. Uses `requirements.agent.txt` with minimal pins.

**`orchestrator/agent_runtime_app.py`** — `AdkApp` wrapper required by Vertex AI Agent Engine. Wraps `root_agent` with session service wiring.

**`orchestrator/app_utils/requirements.agent.txt`** — Minimal requirements for Linux Agent Engine build. `google-adk>=1.33.0,<2.0.0`, `a2a-sdk>=0.3.26,<0.4.0`. No Windows packages, no MCP server packages, no toolbox packages.

**`deployment/terraform/single-project/`** — Terraform for: IAM bindings, Cloud Run service config, BigQuery dataset, Firestore, telemetry resources.

**`deployment/policy.yaml`** — Agent Gateway runtime policy: routes MCP + A2A traffic.

**`Dockerfile`** — Multi-stage build. Builds the FastMCP server (bi-tools-server) for Cloud Run. Lives at repo root — `--source .` is required for `gcloud run deploy`.

### Tests

**`tests/test_rag_corpus.py`** — Uploads a test document, retrieves it, prints score and text. Smoke test for RAG corpus write + read.

**`tests/test_firestore_hitl.py`** — Calls `escalate_hitl` twice for the same query. Call 1: expects `action=regenerate`. Call 2: expects `action=hitl` + Firestore write.

**`tests/test_rag_and_analysis_flow.py`** — Two end-to-end cases: (1) new question → RAG miss → MCP analysis → score → log → RAG hit; (2) multi-question → RAG miss → KPI + anomaly → score → log. Uses real Cloud Run MCP URL and real Vertex AI RAG corpus.

---

## 9. Portfolio Website Section

### Short Version (~50 words)

> **Agentic BI System** — A 5-agent production system on Google's Gemini Enterprise platform. Natural language → BigQuery analytics, with a Gemini-as-judge audit gate that scores every answer before the user sees it. Self-improves: approved answers are cached in Vertex AI RAG; the same question never hits BigQuery twice. Deployed on Vertex AI Agent Engine + Cloud Run.

### Long Version (~200 words)

> **Agentic Business Intelligence System** — Built to demonstrate every layer of modern agentic AI engineering, from custom MCP tools to production infrastructure.
>
> Five specialized agents collaborate on a strict pipeline: a RAG cache agent checks a Vertex AI knowledge base first; on a miss, a specialist agent runs BigQuery analytics or external research; a dedicated audit agent — running as an independent A2A microservice on Cloud Run — scores every answer using Gemini as a judge before the user sees it. Answers scoring ≥ 0.8 are automatically written back to the corpus with a 24-hour TTL stamp, making the system progressively faster and cheaper: common questions skip BigQuery entirely after the first hit.
>
> The system implements all three MCP patterns: a custom 7-tool FastMCP server (SELECT-only SQL, Z-score anomaly detection, KPI summaries, async export, RAG read/write), prebuilt MCP Toolbox for Databases (3 BigQuery tools, zero SQL boilerplate), and ADK's built-in Google Search. A 6-layer wrong-tool defense — callbacks, plugins, Pydantic models, and eval trajectory — ensures correctness before deployment. Infrastructure is Terraform-managed: Vertex AI Agent Engine, Cloud Run, Firestore HITL queue, and a BigQuery analytics table that logs every tool call for weekly prompt optimization.
>
> **Stack:** Google ADK 1.33 · Gemini 2.5 Flash · FastMCP · Vertex AI RAG Engine · BigQuery · Cloud Run · Firestore · Terraform · OpenTelemetry → Langfuse
>
> **Links:** [GitHub](https://github.com/Madhan-mohan14/Agentic-BI) · [Agent Engine Playground](https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/us-central1/agent-engines/8958181377107296256/playground?project=agentic-bi-497010)
