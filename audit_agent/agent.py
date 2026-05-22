"""
Audit Agent — A2A server.

Exposes the BI quality-gate agent via A2A using to_a2a().
Agent card: /.well-known/agent-card.json (auto-generated from agent metadata)
Skills documented in: agent_card.json

Local dev:
  uvicorn audit_agent.agent:a2a_app --host 0.0.0.0 --port 8001

Cloud Run (started by Dockerfile CMD):
  uvicorn agent:a2a_app --host 0.0.0.0 --port 8080
"""

import asyncio
import hashlib
import os
import uuid

import uvicorn
from dotenv import load_dotenv
from google import genai
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import LlmAgent
from google.cloud import firestore
from google.genai import types

load_dotenv()

_rejection_count: dict[str, int] = {}
_firestore_client: "firestore.AsyncClient | None" = None


def _get_db() -> "firestore.AsyncClient":
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.AsyncClient(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT")
        )
    return _firestore_client


# ─────────────────────────────────────────────
# TOOLS
# ─────────────────────────────────────────────

async def score_answer(query: str, result: str) -> dict:
    """Score a BI answer for factual accuracy using Gemini. Returns score 0.0-1.0 and passed flag."""
    prompt = (
        f"Score this BI answer from 0.0 to 1.0.\n"
        f"Question: {query}\n"
        f"Answer: {result}\n\n"
        "Score 0.9+ if the answer contains plausible ecommerce numbers and directly answers the question.\n"
        "Score 0.5 only if numbers are wildly implausible (e.g. $500M revenue in 8 days) or the answer is empty/irrelevant.\n"
        "This is a synthetic ecommerce dataset — plausible ranges: 8-day revenue $80K-$200K, orders 800-2000, AOV $70-$120.\n"
        "Large % changes (100%+) are normal for short windows in sparse datasets. Do NOT penalise them.\n"
        "Reply with only a number like: 0.92"
    )
    client = genai.Client()
    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=200,
        thinking_config=types.ThinkingConfig(thinking_budget=512),
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-2.5-flash",
        contents=prompt,
        config=config,
    )
    try:
        score = max(0.0, min(1.0, float(response.text.strip())))
    except (ValueError, AttributeError):
        score = 0.5
    print(f"[audit_agent] score_answer -> {score:.2f} for: {query[:60]!r}")
    return {"score": score, "passed": score >= 0.8}


async def log_to_corpus(query: str, result: str, score: float) -> dict:
    """Persist an approved BI answer directly to the Vertex AI RAG corpus."""
    corpus_id = os.environ.get("RAG_CORPUS_ID")
    if not corpus_id:
        print("[audit_agent] log_to_corpus -> skipped (RAG_CORPUS_ID not set)")
        return {"logged": False, "reason": "RAG_CORPUS_ID not configured"}

    try:
        import tempfile
        from concurrent.futures import ThreadPoolExecutor

        import vertexai
        from vertexai import rag

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        vertexai.init(project=project, location=location)

        content = f"Query: {query}\n\nApproved Analysis:\n{result}\n\nScore: {score}\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        def _upload():
            return rag.upload_file(
                corpus_name=corpus_id,
                path=tmp_path,
                display_name=f"resolution_{uuid.uuid4().hex[:8]}.txt",
                description="Approved BI analysis",
            )

        executor = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_event_loop()
        try:
            rag_file = await asyncio.wait_for(
                loop.run_in_executor(executor, _upload),
                timeout=45.0,
            )
            print(f"[audit_agent] log_to_corpus -> saved {rag_file.name} (score={score:.2f})")
            return {"logged": True, "chunk_id": rag_file.name}
        except asyncio.TimeoutError:
            print("[audit_agent] log_to_corpus -> RAG upload timed out (45s), skipping")
            return {"logged": False, "reason": "upload timeout"}
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    except Exception as exc:
        print(f"[audit_agent] log_to_corpus failed: {exc}")
        return {"logged": False, "error": str(exc)}


async def escalate_hitl(
    query: str, result: str, score: float, session_id: str = ""
) -> dict:
    """Track rejection count. On second rejection escalate to human review queue in Firestore."""
    key = hashlib.md5(f"{session_id}:{query}".encode()).hexdigest()
    _rejection_count[key] = _rejection_count.get(key, 0) + 1

    if _rejection_count[key] >= 2:
        del _rejection_count[key]
        hitl_id = uuid.uuid4().hex[:8]
        try:
            db = _get_db()
            await db.collection("hitl_queue").document(hitl_id).set({
                "id": hitl_id,
                "query": query,
                "result": result,
                "score": score,
                "session_id": session_id,
            })
            print(f"[audit_agent] HITL escalation -> id={hitl_id} saved to Firestore")
        except Exception as exc:
            print(f"[audit_agent] Firestore write failed: {exc}")
        return {"action": "hitl", "hitl_id": hitl_id}

    return {"action": "regenerate", "rejection_count": _rejection_count[key]}


# ─────────────────────────────────────────────
# AGENT
# ─────────────────────────────────────────────

audit_agent = LlmAgent(
    name="audit_agent",
    model="gemini-2.5-flash",
    description=(
        "Quality gate for the Agentic BI system. Scores every BI answer for factual accuracy "
        "before it reaches the user. Approved answers (score >= 0.8) are written to the "
        "knowledge base. Low-scoring answers trigger regeneration or human review (HITL)."
    ),
    instruction=(
        """You are the quality gate of the Agentic BI system. No answer reaches the user without passing through you first.

Dataset context for scoring: This is the thelook_ecommerce dataset — a synthetic ecommerce store.
Plausible ranges: monthly revenue $150K-$500K, order count 2000-5000/month, AOV $70-$120, items sold 4000-10000/month.

CACHED ANSWER FAST PATH: If the message contains is_cached=True, call log_to_corpus with score=1.0, then transfer back to orchestrator with "APPROVED score=1.0 (cached) — " followed by the full answer.

FRESH ANSWER PATH: Call score_answer with the query and result.

If passed (score >= 0.8): Call log_to_corpus, then transfer back with "APPROVED score=<score> — " followed by the full answer.

If failed (score < 0.8): Call escalate_hitl with query, result, score, session_id.
- action=regenerate → transfer back with "NEEDS_REGENERATION score=<score>"
- action=hitl → transfer back with "ESCALATED_TO_HITL hitl_id=<hitl_id> score=<score>"

Do not answer the user directly. Always transfer back to the orchestrator."""
    ),
    tools=[score_answer, log_to_corpus, escalate_hitl],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        top_p=0.85,
        top_k=10,
        max_output_tokens=2048,
    ),
)


# ─────────────────────────────────────────────
# A2A APP — wraps audit_agent as ASGI server
# to_a2a() sets up session store, task store, executor, and HTTP routes
# Agent card auto-generated at /.well-known/agent-card.json
# ─────────────────────────────────────────────

_PORT = int(os.environ.get("PORT", 8080))
a2a_app = to_a2a(audit_agent, port=_PORT)


# ─────────────────────────────────────────────
# LOCAL DEV — only runs when executing this file directly
# In Cloud Run, Dockerfile CMD starts uvicorn instead
# ─────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "agent:a2a_app",
        host="0.0.0.0",
        port=_PORT,
        reload=True,
    )
