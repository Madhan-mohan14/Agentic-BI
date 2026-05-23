# Deployment Notes — Agentic BI

## Deploy Checklist (in order)

1. **RAG Corpus** — run once per project:
   ```bash
   python deployment/setup_rag_corpus.py
   ```
   Writes `RAG_CORPUS_ID` to `.env`. Required for tools 6 & 7.

2. **Staging Bucket** — auto-created by `deploy.py` if `GCP_STAGING_BUCKET` not set.
   Default name: `{project_id}-agentic-bi-staging`

3. **Agent Engine** — deploy orchestrator:
   ```bash
   python deployment/deploy.py --mode create
   ```
   Writes `AGENT_ENGINE_ID` to `.env` and `deployment_metadata.json`.

4. **Session URI** — after Agent Engine deploy, use Vertex AI sessions in adk web:
   ```bash
   adk web --session_service_uri="agentengine://<AGENT_ENGINE_ID>"
   ```

5. **Registry validation**:
   ```bash
   python deployment/register_tools.py
   ```

---

## Session Backend Options

| Backend | How | When |
|---------|-----|------|
| SQLite (default) | `adk web` with no flags | Local dev only |
| Vertex AI Sessions | `adk web --session_service_uri="agentengine://<id>"` | After Agent Engine deploy |
| In-memory (code) | `LOCAL_DEV=true` in `.env` | Unit tests |
| PostgreSQL | Not implemented — use Vertex AI Sessions in prod | N/A |

---

## Memory Service Options

| Backend | Status | Notes |
|---------|--------|-------|
| InMemoryMemoryService | ✅ active | Local + current prod |
| VertexAiMemoryBankService | 🔜 TODO | Swap in `orchestrator/app.py` once class name verified |

---

## Known Errors to Avoid

| Error | Cause | Fix |
|-------|-------|-----|
| `AGENT_ENGINE_RESOURCE_NAME uses project NUMBER` | Using project ID in resource name | Use project number, not ID |
| `VertexAiSessionService takes only project + location` | Extra params passed | Remove all except `project` + `location` |
| `StreamableHTTPConnectionParams has no headers param` | Tried to pass IAM auth header | Not supported — use unauthenticated Cloud Run or IAM via service identity |
| `agent_engines.create() extra_packages must be local dirs` | Passing pip packages | Only pass local folder paths |

---

## Architecture

```
Agent Engine (orchestrator)
├── sub_agents: rag / analysis / data / research  (in-process)
└── RemoteA2aAgent → audit-agent-service (Cloud Run A2A)
                   → bi-tools-server (Cloud Run MCP — SSE/HTTP)
```

Cloud Run services in `$GOOGLE_CLOUD_PROJECT`, `us-central1`:
- `bi-tools-server` — FastMCP 7 tools, BigQuery reads
- `audit-agent-service` — A2A, score_answer, log_resolution, escalate_hitl
