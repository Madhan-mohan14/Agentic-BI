# Agentic Business Intelligence System

Production-grade Agentic AI system built on the **Google Gemini Enterprise Agent Platform** — showcasing all 4 pillars: **Build · Scale · Govern · Optimize**.

Built as Project 3 for the Google Cloud Next '26 portfolio.

---

## Architecture

```
User
 └── orchestrator (LlmAgent · gemini-2.5-flash · Agent Engine)
      ├── rag_agent        — knowledge cache lookup (Vertex AI RAG Engine)
      ├── analysis_agent   — KPI summaries + anomaly detection via FastMCP
      ├── data_agent        — SQL queries on BigQuery via FastMCP
      ├── research_agent   — Google Search grounding for "why" questions
      └── audit_agent      — A2A quality gate on Cloud Run (score ≥ 0.8 to approve)
```

**Query flow:** User → orchestrator → rag_agent (cache check) → specialist agent → audit_agent → User

---

## 4-Pillar Coverage

| Pillar | Components |
|--------|-----------|
| **Build** | ADK orchestrator + 5 sub-agents, FastMCP 7-tool server, Vertex AI RAG Engine, BigQuery public dataset |
| **Scale** | Cloud Run (MCP server + audit A2A), Agent Engine (orchestrator), Vertex AI Session Service |
| **Govern** | Audit agent (Gen AI Eval), HITL escalation queue, Agent Gateway policy, Model Armor hooks |
| **Optimize** | ADK eval suite (21 cases + 3 personas), OTel → Langfuse tracing, weekly prompt optimizer |

---

## MCP Tools (FastMCP · 7 tools)

| Tool | Purpose |
|------|---------|
| `execute_sql` | SELECT-only BigQuery queries on thelook_ecommerce |
| `detect_anomaly` | Z-score outlier detection |
| `generate_kpi_summary` | Revenue / orders / AOV vs prior period |
| `create_report_job` | Async export to CSV / PDF / Excel |
| `get_schema_context` | Table + column descriptions (called before every SQL) |
| `log_resolution` | Upload approved answers to RAG corpus |
| `search_knowledge_base` | Semantic retrieval from RAG corpus |

---

## Project Structure

```
orchestrator/          # Root agent + app runner
sub_agents/
  rag_agent/           # Cache lookup via RAG Engine
  analysis_agent/      # KPI + anomaly via MCP
  data_agent/          # SQL via MCP
  research_agent/      # Google Search grounding
audit_agent/           # A2A service on Cloud Run
tools/
  bi_tools_server.py   # FastMCP server (7 tools)
  callbacks.py         # before/after tool callbacks
  plugin.py            # BIAgentPlugin error handling
  observability.py     # OTel → Langfuse tracing
eval/
  bi_evalset.test.json # 21 trajectory test cases
  eval_config.json     # ADK eval config
  scenarios/           # novice / expert / evaluator personas
  optimize.py          # Weekly low-score trace optimizer
deployment/
  deploy.py            # Agent Engine create / update
  setup_rag_corpus.py  # Create Vertex AI RAG corpus
  policy.yaml          # Agent Gateway runtime policy
  agent_registry.yaml  # Agent + tool registry
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set environment variables
```bash
cp .env.example .env
# Fill in your values
```

Required variables:
```
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_API_KEY=your-gemini-api-key
AUDIT_A2A_URL=https://audit-agent-service-....run.app
MCP_URL=https://bi-tools-server-....run.app/mcp
RAG_CORPUS_ID=projects/.../ragCorpora/...
```

### 3. Run locally
```bash
# Start the FastMCP server first
MCP_TRANSPORT=http MCP_PORT=8088 python tools/bi_tools_server.py

# In a separate terminal, start the ADK web UI
adk web
```

### 4. Run evals
```bash
adk eval eval/bi_evalset.test.json --config eval/eval_config.json
```

---

## Deployment

### Audit Agent (A2A on Cloud Run)
```bash
cd audit_agent
gcloud run deploy audit-agent-service --source . --region us-central1
```

### MCP Server (Cloud Run)
```bash
gcloud run deploy bi-tools-server --source . --region us-central1
```

### ADK Web UI (Cloud Run)
```bash
gcloud builds submit --config cloudbuild-ui.yaml
gcloud run deploy a2ui --image gcr.io/$PROJECT_ID/a2ui:latest --region us-central1
```

### Orchestrator (Agent Engine)
```bash
python deployment/deploy.py --mode create
```

---

## Data Sources

- **Primary:** `bigquery-public-data.thelook_ecommerce` — orders, order_items, inventory_items, users, products
- **Secondary:** `bigquery-public-data.ga4_obfuscated_sample_ecommerce` — funnel + traffic analysis
- **RAG Store:** Vertex AI RAG Engine (approved answers corpus, grows with each query)

---

## Tech Stack

- [Google ADK 1.33.0](https://google.github.io/adk-docs)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [Vertex AI RAG Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/rag-overview)
- [A2A Protocol](https://google.github.io/adk-docs/a2a)
- Gemini 2.5 Flash
- BigQuery
- Cloud Run
- Agent Engine
