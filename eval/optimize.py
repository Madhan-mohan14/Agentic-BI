"""
Weekly prompt optimization (OPTIMIZE pillar).

Workflow:
  1. Fetch traces from Langfuse where score < threshold
  2. Group failures by agent name
  3. For each agent, call Gemini with current instruction + failure samples
  4. Print suggested instruction patches for manual review

Run:
  python eval/optimize.py
  python eval/optimize.py --threshold 0.6
"""

import argparse
import os
from collections import defaultdict

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

_LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
_LANGFUSE_PK = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
_LANGFUSE_SK = os.environ.get("LANGFUSE_SECRET_KEY", "")
_GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")


def fetch_low_score_traces(threshold: float = 0.7) -> list[dict]:
    """Return traces from Langfuse where any score < threshold."""
    if not (_LANGFUSE_PK and _LANGFUSE_SK):
        print("[optimize] Langfuse keys not set — skipping trace fetch.")
        return []

    url = f"{_LANGFUSE_HOST}/api/public/traces"
    try:
        resp = httpx.get(
            url,
            auth=(_LANGFUSE_PK, _LANGFUSE_SK),
            params={"limit": 100},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        traces = data.get("data", [])
        low = [t for t in traces if any(
            s.get("value", 1.0) < threshold for s in t.get("scores", [])
        )]
        print(f"[optimize] Found {len(low)} low-score traces (threshold={threshold}) out of {len(traces)} total.")
        return low
    except Exception as exc:
        print(f"[optimize] Failed to fetch traces: {exc}")
        return []


def analyze_failures(traces: list[dict]) -> dict[str, list[str]]:
    """Group failure inputs by agent name extracted from trace metadata."""
    groups: dict[str, list[str]] = defaultdict(list)
    for trace in traces:
        name = trace.get("name", "unknown")
        user_input = trace.get("input", {})
        if isinstance(user_input, dict):
            text = user_input.get("messages", [{}])[0].get("content", str(user_input))
        else:
            text = str(user_input)
        groups[name].append(text[:300])
    return dict(groups)


def suggest_prompt_patch(agent_name: str, failure_samples: list[str]) -> str:
    """Ask Gemini to suggest an instruction improvement for the failing agent."""
    if not _GOOGLE_API_KEY:
        return "[skip] GOOGLE_API_KEY not set."

    client = genai.Client(api_key=_GOOGLE_API_KEY)
    samples_text = "\n".join(f"- {s}" for s in failure_samples[:5])
    prompt = (
        f"You are reviewing a low-performing AI agent named '{agent_name}'.\n\n"
        f"These user inputs caused low quality responses (score < threshold):\n{samples_text}\n\n"
        f"Suggest a concise improvement to the agent's instruction that would help it handle "
        f"these cases better. Output only the suggested instruction change (2-4 sentences max). "
        f"Do not rewrite the full instruction."
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=256),
    )
    return resp.text.strip()


def main(threshold: float = 0.7) -> None:
    print(f"\n=== Agentic BI Prompt Optimization (threshold={threshold}) ===\n")

    traces = fetch_low_score_traces(threshold)
    if not traces:
        print("No low-score traces found. System is performing well or Langfuse is not configured.")
        return

    groups = analyze_failures(traces)

    for agent_name, samples in groups.items():
        print(f"\n--- Agent: {agent_name} ({len(samples)} failures) ---")
        patch = suggest_prompt_patch(agent_name, samples)
        print(f"Suggested patch:\n{patch}")

    print("\n[optimize] Review patches above. Apply manually if they look correct.")
    print("[optimize] Re-run adk eval after applying patches to verify improvement.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly prompt optimization")
    parser.add_argument("--threshold", type=float, default=0.7, help="Score threshold (default 0.7)")
    args = parser.parse_args()
    main(args.threshold)
