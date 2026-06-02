"""
Test that rag_agent returns is_cached=True for questions already in the corpus.

Prerequisites: bi_tools_server running on port 8088.
  python tools/bi_tools_server.py

Run: python tests/test_rag_agent_cache.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".")

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


async def main():
    from sub_agents.rag_agent.agent import rag_agent

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="rag_cache_test", user_id="test"
    )
    runner = Runner(
        agent=rag_agent,
        app_name="rag_cache_test",
        session_service=session_service,
    )

    questions = [
        "what are the KPIs for the last 6 days",
        "what are the KPIs for the last 2 days",
        "top 5 product categories by revenue",
    ]

    hits = 0
    for q in questions:
        print(f"\nQ: {q!r}")
        content = types.Content(role="user", parts=[types.Part.from_text(text=q)])

        final_text = ""
        async for event in runner.run_async(
            user_id="test",
            session_id=session.id,
            new_message=content,
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    final_text = event.content.parts[0].text or ""

        if "is_cached=True" in final_text:
            print(f"  CACHE HIT")
            print(f"  {final_text[:200]}")
            hits += 1
        elif "ANSWER NOT FOUND" in final_text:
            print(f"  CACHE MISS")
        else:
            print(f"  UNEXPECTED: {final_text[:200]}")

    print(f"\n{'='*40}")
    print(f"Result: {hits}/{len(questions)} cache hits")
    if hits == len(questions):
        print("ALL PASS — rag_agent cache is working")
    else:
        print("SOME MISSES — cache not fully working")


asyncio.run(main())
