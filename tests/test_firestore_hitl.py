"""
Verify Firestore HITL escalation write.

Prerequisites:
  gcloud firestore databases create --project=agentic-bi-497010 --location=nam5

Run: python tests/test_firestore_hitl.py
Success: Call 2 prints action=hitl + document appears in Firestore console.
Console: https://console.cloud.google.com/firestore/data/hitl_queue
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, ".")
from audit_agent.agent import escalate_hitl


async def main():
    print("Testing Firestore HITL escalation...\n")

    # First call — same query, first rejection → should return action=regenerate
    r1 = await escalate_hitl(
        query="test query for firestore verification",
        result="test result",
        score=0.5,
        session_id="test-session-verify",
    )
    print(f"Call 1: {r1}")

    if r1.get("action") != "regenerate":
        print(f"UNEXPECTED: expected action=regenerate on first call, got {r1}")

    # Second call same query — should trigger Firestore write
    r2 = await escalate_hitl(
        query="test query for firestore verification",
        result="test result",
        score=0.5,
        session_id="test-session-verify",
    )
    print(f"Call 2: {r2}")

    if r2.get("action") == "hitl":
        hitl_id = r2.get("hitl_id", "unknown")
        print(f"\nFirestore HITL: OK — id={hitl_id}")
        print(f"Verify at: https://console.cloud.google.com/firestore/data/hitl_queue/{hitl_id}")
    else:
        print("\nFAIL: expected action=hitl on second call")
        print("Make sure Firestore database exists:")
        print("  gcloud firestore databases create --project=agentic-bi-497010 --location=nam5")
        sys.exit(1)


asyncio.run(main())
