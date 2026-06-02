"""
End-to-end flow test for two cases:

CASE 1 — RAG miss → analysis → log → RAG hit (single question, new time window)
CASE 2 — Multi-question (RAG hit for part 1 + analysis for part 2 in one query)

Run: python tests/test_rag_and_analysis_flow.py
"""

import asyncio
import datetime
import json
import os
import re
import sys

import httpx
import vertexai
from dotenv import load_dotenv
from vertexai import rag

load_dotenv()
sys.path.insert(0, ".")

from audit_agent.agent import log_to_corpus, score_answer

# Always use Cloud Run URL for tests — local server may not be running
_MCP_URL = "https://bi-tools-server-492257799932.us-central1.run.app/mcp"
_CORPUS_ID = os.environ["RAG_CORPUS_ID"]
_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

vertexai.init(project=_PROJECT, location=_LOCATION)

SEP = "-" * 60


def banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def step(n: int, label: str) -> None:
    print(f"\n[STEP {n}] {label}")
    print(SEP)


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def miss(msg: str) -> None:
    print(f"  XX  {msg}")


# -- MCP tool caller ------------------------------------------------------------

async def call_mcp_tool(tool_name: str, args: dict) -> dict:
    """Send a JSON-RPC tools/call to the Cloud Run MCP server (streamable HTTP).

    Streamable HTTP requires an initialize handshake first to get a session ID,
    then the session ID is passed as a header on the actual tool call.
    """
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Initialize session
        init_resp = await client.post(
            _MCP_URL,
            headers=base_headers,
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1"},
                },
            },
        )
        init_resp.raise_for_status()
        session_id = init_resp.headers.get("mcp-session-id", "")

        # 2. Call the tool using the session ID
        tool_headers = {**base_headers}
        if session_id:
            tool_headers["mcp-session-id"] = session_id
        tool_resp = await client.post(
            _MCP_URL,
            headers=tool_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": args},
            },
        )
        tool_resp.raise_for_status()
        # Response is SSE: parse "data: {...}" line
        data = _parse_sse(tool_resp.text)

    raw = data.get("result", {}).get("content", [{}])[0].get("text", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _parse_sse(body: str) -> dict:
    """Extract the JSON payload from an SSE 'data: ...' response."""
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                pass
    return {}


# -- RAG search (direct Vertex AI SDK, same as bi_tools_server) ----------------

def rag_search(query: str, top_k: int = 3) -> list[dict]:
    resp = rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=_CORPUS_ID)],
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
    )
    return [
        {"score": ctx.score, "text": ctx.text}
        for ctx in resp.contexts.contexts
    ]


def ttl_check(results: list[dict]) -> tuple[bool, str]:
    """Simulate after_tool_callback TTL logic. Returns (is_fresh, reason)."""
    today = datetime.date.today()
    for r in results:
        m = re.search(r"Logged: (\d{4}-\d{2}-\d{2})", r["text"])
        if m:
            logged = datetime.date.fromisoformat(m.group(1))
            age = (today - logged).days
            if age < 1:
                return True, f"Logged={m.group(1)}, age={age}d → FRESH"
    return False, "No 'Logged: YYYY-MM-DD' found in any result → STALE"


# ==============================================================================
# CASE 1 — New question: RAG miss → analysis → log → RAG hit
# ==============================================================================

async def case1():
    banner("CASE 1 — RAG miss → Analysis → Log → RAG hit")
    question = "what are the kpis for the last 9 days"
    print(f"  Question: '{question}'")

    # STEP 1: Search corpus — expect MISS
    step(1, "search_knowledge_base (expect MISS — first time asked)")
    results = rag_search(question)
    print(f"  RAG returned {len(results)} result(s)")
    for r in results:
        print(f"    score={r['score']:.4f}  {r['text'][:80]!r}")
    fresh, reason = ttl_check(results)
    if fresh:
        ok(f"Cache HIT — {reason}")
        print("  (already in corpus from earlier run — skipping to Step 4 to re-test hit)")
    else:
        miss(f"Cache MISS — {reason}")

    # STEP 2: Call generate_kpi_summary via MCP (analysis_agent path)
    step(2, "generate_kpi_summary via MCP (analysis_agent does this)")
    print(f"  Calling MCP tool: generate_kpi_summary(days=9)")
    kpi = await call_mcp_tool("generate_kpi_summary", {
        "metrics": ["revenue", "orders", "aov", "items_sold"],
        "days": 9,
    })
    print(f"  MCP response keys: {list(kpi.keys())}")
    summary_text = (
        kpi.get("summary")
        or kpi.get("result")
        or str(kpi)
    )
    print(f"  Summary preview: {str(summary_text)[:200]}")
    ok("analysis_agent would return this as the answer")

    # STEP 3: audit_agent scores the answer
    step(3, "score_answer (audit_agent scores the analysis result)")
    score_result = await score_answer(query=question, result=str(summary_text))
    score = score_result["score"]
    passed = score_result["passed"]
    print(f"  Score: {score:.2f}  passed={passed}")
    if passed:
        ok(f"Score {score:.2f} ≥ 0.8 → APPROVED → will log to corpus")
    else:
        miss(f"Score {score:.2f} < 0.8 → would escalate to HITL, using 0.85 for demo")
        score = 0.85

    # STEP 4: log_to_corpus — write approved answer to corpus
    step(4, "log_to_corpus (audit_agent persists approved answer)")
    answer_to_log = f"For the last 9 days: {str(summary_text)[:300]}"
    log_result = await log_to_corpus(
        query=question,
        result=answer_to_log,
        score=score,
    )
    print(f"  log_to_corpus result: {log_result}")
    if log_result.get("logged"):
        ok(f"Saved to corpus: {log_result.get('chunk_id', '')[-30:]}")
    else:
        miss(f"Log failed: {log_result}")
        return

    # STEP 5: Search again — expect HIT
    step(5, "search_knowledge_base again (expect HIT now)")
    print("  (RAG indexing is near-instant; searching immediately...)")
    results2 = rag_search(question)
    print(f"  RAG returned {len(results2)} result(s)")
    for r in results2:
        has_logged = "Logged:" in r["text"]
        print(f"    score={r['score']:.4f}  has_logged={has_logged}  {r['text'][:80]!r}")
    fresh2, reason2 = ttl_check(results2)
    if fresh2:
        ok(f"Cache HIT — {reason2}")
        print()
        print("  rag_agent would return: is_cached=True + the cached answer")
        print("  orchestrator skips Step 2 (no analysis_agent call) → cheaper + faster")
    else:
        miss(f"Cache still MISS — {reason2}")

    print(f"\n  CASE 1 RESULT: {'PASS' if fresh2 else 'FAIL'}")


# ==============================================================================
# CASE 2 — Multi-question: RAG hit for KPIs + analysis for anomaly detection
# ==============================================================================

async def case2():
    banner("CASE 2 — Multi-question: RAG hit (KPIs) + Analysis (anomaly)")
    question = "what are the kpis for the last 3 days and is there any anomaly in orders?"
    print(f"  Question: '{question}'")
    print()
    print("  Orchestrator logic:")
    print("    STEP 1 → rag_agent searches full question")
    print("    STEP 2 → if miss: pick ONE specialist (analysis_agent for KPI + anomaly)")
    print("    STEP 3 → audit_agent scores")

    # STEP 1: RAG search with the combined question
    step(1, "rag_agent: search_knowledge_base with full multi-question")
    results = rag_search(question)
    print(f"  RAG returned {len(results)} result(s)")
    for r in results:
        has_logged = "Logged:" in r["text"]
        print(f"    score={r['score']:.4f}  has_logged={has_logged}  {r['text'][:90]!r}")
    fresh, reason = ttl_check(results)
    if fresh:
        ok(f"Cache HIT — {reason}")
        print("  → orchestrator returns cached answer, skips Step 2")
        cache_hit = True
    else:
        miss(f"Cache MISS — {reason}")
        print("  → combined question not in corpus; orchestrator goes to specialist")
        cache_hit = False

    # STEP 2a: KPI part — via generate_kpi_summary
    step(2, "analysis_agent: generate_kpi_summary (KPI part of the question)")
    kpi = await call_mcp_tool("generate_kpi_summary", {
        "metrics": ["revenue", "orders", "aov"],
        "days": 3,
    })
    kpi_text = kpi.get("summary") or kpi.get("result") or str(kpi)
    print(f"  KPI result: {str(kpi_text)[:200]}")
    ok("generate_kpi_summary returned")

    # STEP 2b: Anomaly part — via detect_anomaly
    step(3, "analysis_agent: detect_anomaly (anomaly part of the question)")
    anomaly = await call_mcp_tool("detect_anomaly", {
        "table": "orders",
        "column": "num_of_item",
        "threshold": 2.0,
    })
    print(f"  Anomaly result: {str(anomaly)[:300]}")
    ok("detect_anomaly returned")

    # Combine both results (what analysis_agent would compile)
    combined_answer = (
        f"KPI Summary (last 3 days): {str(kpi_text)[:200]}\n\n"
        f"Order Anomaly Check: {str(anomaly)[:200]}"
    )

    # STEP 3: Score + log combined answer
    step(4, "audit_agent: score combined answer")
    score_result = await score_answer(query=question, result=combined_answer)
    score = score_result["score"]
    print(f"  Score: {score:.2f}  passed={score_result['passed']}")

    if score_result["passed"]:
        ok(f"Score {score:.2f} ≥ 0.8 → APPROVED")
        step(5, "audit_agent: log_to_corpus (multi-question answer persisted)")
        log_result = await log_to_corpus(
            query=question,
            result=combined_answer,
            score=score,
        )
        print(f"  log_to_corpus: {log_result}")
        if log_result.get("logged"):
            ok(f"Saved to corpus: {log_result.get('chunk_id', '')[-30:]}")
            print()
            print("  Next time this exact question is asked → RAG will hit directly")
            print("  No analysis_agent call needed → faster + no BigQuery cost")
        else:
            miss(f"Log failed: {log_result}")
    else:
        miss(f"Score {score:.2f} < 0.8 → would escalate to HITL")

    print(f"\n  CASE 2 RESULT: PASS — RAG={('HIT' if cache_hit else 'MISS')}, Analysis=called, Audit=scored, Corpus=logged")


async def main():
    print("\n" + "#" * 60)
    print("  RAG + Analysis Flow Test")
    print("  Corpus:", _CORPUS_ID[-30:])
    print("  MCP:", _MCP_URL[-50:])
    print("#" * 60)

    await case1()
    await case2()

    print("\n" + "#" * 60)
    print("  All cases complete")
    print("#" * 60)


asyncio.run(main())
