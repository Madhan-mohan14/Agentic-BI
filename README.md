<div align="center">

# 🧠 Agentic Business Intelligence System

### *Because dashboards are dead. Agents answer.*

**Production-grade multi-agent AI system built on the [Google Gemini Enterprise Agent Platform](https://cloud.google.com/gemini-enterprise) using the [Agent Development Kit (ADK)](https://adk.dev).**

A self-improving, quality-gated BI assistant that routes natural-language business questions to the right specialist agent, scores every answer before the user sees it, and gets smarter with every query — automatically.

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://python.org)
[![ADK](https://img.shields.io/badge/Google%20ADK-1.33.0-4285F4?logo=google)](https://adk.dev)
[![Gemini](https://img.shields.io/badge/Gemini-2.5%20Flash-8E44AD?logo=google)](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/models)
[![Cloud Run](https://img.shields.io/badge/Cloud%20Run-deployed-34A853?logo=google-cloud)](https://cloud.google.com/run)
[![Agent Engine](https://img.shields.io/badge/Agent%20Engine-live-EA4335?logo=google-cloud)](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine)
[![License](https://img.shields.io/badge/License-Apache%202.0-yellow)](LICENSE)

</div>

---

## 🎯 What Is This?

This isn't a chatbot wrapper around an LLM. This is a **production-grade agentic system** that demonstrates every layer of modern AI engineering:

- **Writes its own MCP tools** from scratch using FastMCP — a full 7-tool Model Context Protocol server
- **Consumes prebuilt MCP tools** via MCP Toolbox for Databases (BigQuery Layer 2)
- **Uses ADK's built-in tools** — Google Search grounded directly into a sub-agent
- **Implements the A2A protocol** — the audit agent runs as a standalone A2A microservice on Cloud Run, consumed remotely by the orchestrator
- **Deploys to Vertex AI Agent Engine** — the orchestrator lives in managed infrastructure with persistent session state
- **Self-improves** — every approved answer is written back to a Vertex AI RAG corpus; future identical questions skip BigQuery entirely

Built as **Project 3** for a Google Cloud Next '26 Agentic AI portfolio, covering all **4 pillars: Build · Scale · Govern · Optimize**.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    USER QUERY                           │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│           ORCHESTRATOR  (Vertex AI Agent Engine)        │
│           gemini-2.5-flash · ADK LlmAgent              │
│           Safety filter · BigQuery analytics plugin     │
└──┬──────────┬──────────┬──────────┬──────────┬──────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
 rag_agent  data_agent  analysis  research   audit_agent
 (cache     (SQL via    _agent    _agent     (A2A · Cloud
  lookup)    FastMCP)   (KPI +    (Google     Run · quality
             Layer 1)   anomaly   Search)     gate)
                        FastMCP)
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
 Vertex AI  BigQuery   BigQuery   Google    Firestore
 RAG Engine public     public     Search    HITL Queue
 (corpus)   dataset    dataset    API
```

**Every answer goes through this exact pipeline — no exceptions:**

```
User NL → orchestrator → rag_agent (cache check)
        → specialist agent (only on cache miss)
        → audit_agent (score ≥ 0.8 to approve)
        → log_to_corpus (approved answers teach future queries)
        → User
```

---

## 🧩 Three Layers of MCP — All in One Project

This project is one of the few public examples that implements **all three MCP patterns simultaneously**:

### Layer 1 — Custom FastMCP Server (written from scratch)
7 tools built with [FastMCP](https://github.com/jlowin/fastmcp), served over streamable-HTTP on Cloud Run:

| Tool | What It Does |
|------|-------------|
| `execute_sql` | SELECT-only BigQuery queries — auto-appends LIMIT, blocks mutations |
| `detect_anomaly` | Z-score outlier flagging with a validated column allowlist |
| `generate_kpi_summary` | Revenue / orders / AOV / items sold vs. prior period |
| `create_report_job` | Async export queue — CSV, PDF, Excel — returns job ID + ETA |
| `get_schema_context` | Table + column descriptions — **must** be called before any SQL |
| `log_resolution` | Uploads approved answers to Vertex AI RAG corpus |
| `search_knowledge_base` | Semantic retrieval — top-3 results from the RAG corpus |

### Layer 2 — MCP Toolbox for Databases (prebuilt)
3 BigQuery-native tools served by [MCP Toolbox](https://github.com/googleapis/mcp-toolbox-for-databases) — zero SQL boilerplate:
`get_top_products` · `get_revenue_by_period` · `get_inventory_anomalies`

### Layer 3 — ADK Built-in Tools (zero config)
`google_search` — wired directly into `research_agent` with no MCP setup required. The ADK handles auth, retries, and grounding.

---

## 🤝 A2A Protocol — Real Microservice Architecture

The audit agent doesn't just live in the same process. It runs as a **standalone A2A microservice on Cloud Run**, exposing a proper agent card at `/.well-known/agent.json`.

The orchestrator consumes it remotely using `RemoteA2aAgent`:

```python
audit_agent = RemoteA2aAgent(
    name="audit_agent",
    agent_card=_load_audit_card(AUDIT_A2A_URL),
    use_legacy=False,   # A2A 0.2+ protocol
)
```

This means the audit agent can be **independently scaled, versioned, and replaced** without touching the orchestrator. That's real service architecture — not a monolith pretending to be multi-agent.

---

## 🛡️ 6-Layer Wrong-Tool Defense

Getting an LLM to call the right tool in the right order is harder than it sounds. This system has six independent layers to enforce correctness:

| Layer | Where | What It Does |
|-------|-------|-------------|
| **1. before_tool_callback** | All agents | Blocks `execute_sql` until schema is fetched; corrects invalid `table` args; blocks low-score `log_resolution` |
| **2. after_tool_callback** | All agents | Injects `retry_hint` on empty SQL results; flags RAG cache misses |
| **3. BIAgentPlugin.on_tool_error** | Orchestrator | BigQuery timeout → cached fallback; connection refused → helpful hint; quota → `retry_after` |
| **4. BIAgentPlugin.on_model_error** | Orchestrator | Gemini quota / 503 → graceful user-facing message instead of crash |
| **5. Pydantic return models** | MCP server | Malformed tool responses caught at schema boundary |
| **6. ADK eval trajectory** | Pre-deploy | Wrong tool call order caught before it ever reaches production |

---

## 🔁 Self-Improving Memory Loop

Every query makes the system smarter:

```
Query hits rag_agent
    ├── CACHE HIT  → skip BigQuery → audit (score=1.0) → return answer → corpus
    └── CACHE MISS → specialist agent → audit
                          ├── score ≥ 0.8 → log_to_corpus() → corpus grows
                          └── score < 0.8 → escalate_hitl()
                                   └── 2nd rejection → Firestore HITL queue
```

Over time, the RAG corpus accumulates approved answers. Common questions stop hitting BigQuery entirely. The system gets faster and cheaper at scale — without any retraining.

---

## 📊 Evaluation — Not an Afterthought

Every layer of agent behavior is evaluated using **ADK's eval framework** before deployment:

```bash
adk eval eval/bi_evalset.test.json --config eval/eval_config.json
```

**Three eval criteria run on every PR:**

| Criterion | Threshold | What It Catches |
|-----------|-----------|----------------|
| `rubric_based_tool_use_quality_v1` | ≥ 0.7 | Wrong agent routing, missing audit gate, skipped cache check |
| `rubric_based_final_response_quality_v1` | ≥ 0.7 | Vague answers, missing numbers, dataset-ungrounded responses |
| `hallucinations_v1` | ≥ 0.8 | Fabricated metrics, made-up product names, invented revenue figures |

**The eval rubrics are explicit, not vibes:**
- "The agent must call `rag_agent` first before any specialist for every analytics question"
- "The response must include specific numbers grounded in the thelook_ecommerce dataset"
- "audit_agent must be called after every specialist before returning to the user"

**Plus four batch eval sets** covering KPI & customer queries, anomaly detection, SQL routing, and research grounding — all in `eval/`.

---

## 🏛️ 4-Pillar Coverage (Google Cloud Next '26)

| Pillar | What's Built |
|--------|-------------|
| 🔨 **Build** | ADK orchestrator + 5 sub-agents, FastMCP 7-tool server, Vertex AI RAG Engine, BigQuery public dataset (`thelook_ecommerce`), A2A audit microservice |
| 📈 **Scale** | Orchestrator on Vertex AI Agent Engine, MCP server + audit agent on Cloud Run, Vertex AI Session Service for persistent state, Terraform single-project infra |
| ⚖️ **Govern** | LLM-as-judge audit agent (Gemini scores every answer), HITL escalation queue (Firestore), prompt injection + hard-block safety filter, `before_tool_callback` enforcement |
| 🔄 **Optimize** | ADK eval suite (golden set + 4 batch sets), OTel → Langfuse tracing, BigQuery analytics plugin (logs every tool call to `agent_analytics.tool_events`), weekly `optimize.py` pulls traces scoring < 0.7 for prompt patching |

---

## 🗂️ Project Structure

```
orchestrator/
  agent.py               # Root orchestrator — 3-step pipeline, safety filter, A2A wiring
  agent_runtime_app.py   # AdkApp wrapper for Vertex AI Agent Engine deploy
  app_utils/             # Telemetry helpers, typing extensions

sub_agents/
  rag_agent/             # Cache check via Vertex AI RAG Engine
  analysis_agent/        # KPI + anomaly detection via FastMCP Layer 1
  data_agent/            # BigQuery SQL via FastMCP Layer 1; owns MCP server subprocess
  research_agent/        # Google Search grounding (ADK built-in Layer 3)

audit_agent/
  agent.py               # LlmAgent + score_answer + log_to_corpus + escalate_hitl
                         # Exposed as A2A microservice via to_a2a()

tools/
  bi_tools_server.py     # FastMCP server — all 7 Layer 1 tools
  callbacks.py           # before/after tool callbacks (6-layer defense Layers 1 & 2)
  plugin.py              # BIAgentPlugin + BigQueryAnalyticsPlugin (Layers 3 & 4)
  observability.py       # OTel → Langfuse tracing setup
  tools.yaml             # MCP Toolbox config (Layer 2 — 3 BigQuery tools)

eval/
  bi_evalset.test.json   # Golden eval set — full pipeline trajectory cases
  eval_config.json       # rubric_based + hallucinations_v1 criteria
  batch1-4.test.json     # Domain-specific batch eval sets
  optimize.py            # Weekly: pull traces < 0.7, A/B prompt patches

deployment/
  terraform/             # Single-project Terraform — IAM, Cloud Run, BQ, telemetry
  
tests/
  unit/                  # Unit tests
  integration/           # Integration tests (agent + Agent Engine)
  eval/                  # Eval config + evalsets (agents-cli compatible)

deploy_agent_runtime.py  # Custom deploy script — packages orchestrator + sub_agents + tools
```

---

## ⚡ Quick Start

### Prerequisites
- Python 3.12+
- `uv` ([install](https://docs.astral.sh/uv/getting-started/installation/))
- Google Cloud project with Vertex AI + BigQuery + Cloud Run APIs enabled
- `gcloud` CLI authenticated

### 1. Clone & install
```bash
git clone https://github.com/Madhan-mohan14/Agentic-BI.git
cd Agentic-BI
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in your values
```

```env
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_API_KEY=your-gemini-api-key
MCP_URL=http://127.0.0.1:8088/mcp        # or your Cloud Run URL
AUDIT_A2A_URL=https://your-audit-agent.run.app
RAG_CORPUS_ID=projects/.../ragCorpora/...
```

### 3. Run locally

```bash
# Terminal 1 — start the FastMCP server (Layer 1)
MCP_TRANSPORT=http MCP_PORT=8088 python tools/bi_tools_server.py

# Terminal 2 — launch ADK web UI
adk web
```

Open [http://localhost:8000](http://localhost:8000) and ask:
> *"What are the KPIs for the last 30 days?"*
> *"Who are the top 5 customers by revenue?"*
> *"Are there any anomalies in sales this week?"*

### 4. Run evals

```bash
adk eval eval/bi_evalset.test.json --config eval/eval_config.json
```

---

## 🚀 Deployment

### MCP Server → Cloud Run
```bash
gcloud run deploy bi-tools-server \
  --source tools/ \
  --region us-central1 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT_ID
```

### Audit Agent → Cloud Run (A2A)
```bash
gcloud run deploy audit-agent-service \
  --source audit_agent/ \
  --region us-central1
```

### Orchestrator → Vertex AI Agent Engine
```bash
python deploy_agent_runtime.py \
  --mcp-url https://bi-tools-server-<hash>.us-central1.run.app/mcp \
  --audit-url https://audit-agent-service-<hash>.us-central1.run.app
```

### Infrastructure → Terraform
```bash
cd deployment/terraform/single-project
cp vars/env.tfvars.example vars/env.tfvars   # fill in project_id
terraform init && terraform apply
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | [Google ADK 1.33.0](https://adk.dev) |
| Agent Platform | [Gemini Enterprise Agent Platform](https://cloud.google.com/gemini-enterprise) — Vertex AI Agent Engine |
| LLM | Gemini 2.5 Flash |
| MCP Layer 1 | [FastMCP](https://github.com/jlowin/fastmcp) — custom 7-tool server |
| MCP Layer 2 | [MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox-for-databases) |
| MCP Layer 3 | ADK built-in `google_search` |
| Agent Protocol | [A2A Protocol](https://google.github.io/adk-docs/a2a) — audit agent as remote microservice |
| Data | BigQuery public dataset (`thelook_ecommerce`) |
| Memory | [Vertex AI RAG Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/rag-overview) — self-growing corpus |
| Observability | OpenTelemetry → [Langfuse](https://langfuse.com) |
| Analytics | BigQuery (`agent_analytics.tool_events`) — every tool call logged |
| Eval | [ADK Eval](https://adk.dev/evaluate) — rubric-based + hallucination scoring |
| Infrastructure | Terraform + Cloud Run + Firestore |
| Session State | Vertex AI Session Service |

---

## 📈 Why This Architecture Matters

Most "multi-agent" demos are wrappers. They call one LLM, maybe two, and label it agentic. This system is different:

**Real separation of concerns** — each agent has one job, one tool set, and one output key. The orchestrator never touches data directly.

**Real quality control** — a dedicated agent scores every answer before the user sees it, using the same model (Gemini) that produced it, acting as an independent judge.

**Real MCP** — not just calling APIs. A proper MCP server with schema enforcement, transport configuration, and three distinct integration layers.

**Real A2A** — the audit agent exposes a proper agent card and is consumed over HTTP by the orchestrator. Swap it for any other A2A-compatible agent without changing the orchestrator.

**Real infra** — Terraform-managed Cloud Run services, Agent Engine deployment with custom source packaging, session persistence via Vertex AI Session Service.

---

## 🌱 What's Next

- [ ] Model Armor integration (currently implemented as a `before_model_callback` substitute)
- [ ] Streaming responses via ADK Live
- [ ] Multi-turn conversation memory with Memory Bank
- [ ] CI/CD pipeline with `agents-cli infra cicd`
- [ ] GA4 funnel analysis sub-agent

---

<div align="center">

## 🤝 Connect

If this project helped you understand agentic AI architecture, consider giving it a ⭐

**Built with curiosity, late nights, and a lot of love** ❤️

by [Madhan Mohan](https://github.com/Madhan-mohan14)

*Agentic AI / Voice AI Engineer — open to opportunities*

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?logo=linkedin)](https://linkedin.com/in/madhan-mohan14)
[![GitHub](https://img.shields.io/badge/GitHub-Follow-181717?logo=github)](https://github.com/Madhan-mohan14)

---

*Built on [Google ADK](https://adk.dev) · Powered by [Gemini Enterprise Agent Platform](https://cloud.google.com/gemini-enterprise) · Data from [TheLook Ecommerce](https://console.cloud.google.com/marketplace/details/bigquery-public-data/thelook-ecommerce)*

</div>
