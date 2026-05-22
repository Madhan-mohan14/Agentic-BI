import asyncio
import os

from dotenv import load_dotenv
from google import genai
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

load_dotenv()


async def web_search(query: str) -> dict:
    """Search the web for business context and market trends using Google Search grounding."""
    client = genai.Client()
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=f"Search for recent business information about: {query}. Summarize the key findings clearly.",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )
        sources = []
        if response.candidates:
            meta = getattr(response.candidates[0], "grounding_metadata", None)
            if meta and hasattr(meta, "grounding_chunks"):
                for chunk in meta.grounding_chunks[:5]:
                    web = getattr(chunk, "web", None)
                    if web:
                        sources.append({"title": getattr(web, "title", ""), "url": getattr(web, "uri", "")})
        return {"summary": response.text, "sources": sources}
    except Exception as exc:
        return {"summary": "", "error": str(exc)}


def _after_research_agent(callback_context: CallbackContext) -> None:
    result = callback_context.state.get("research_result", "")
    print(f"[after_agent] research_agent completed - {len(result)} chars")


research_agent = LlmAgent(
    name="research_agent",
    model="gemini-2.5-flash",
    description=(
        "Research agent for explaining business trends using live web data. "
        "Call this for 'why' questions — why revenue dropped, what caused an anomaly, "
        "what market trends explain a pattern. Do NOT use for BigQuery data or KPI calculations."
    ),
    instruction=(
        """You are the research agent for an ecommerce BI system. You explain WHY business trends happen using live web data.

Call web_search with a focused query about the business topic the user asked about.
Synthesize the results into a clear explanation of the likely causes — be specific, not generic.
If web_search fails, answer from training knowledge.

Write your complete analysis and stop. The orchestrator takes it from here."""
    ),
    tools=[web_search],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
        top_p=0.95,
        max_output_tokens=2048,
    ),
    output_key="research_result",
    after_agent_callback=_after_research_agent,
)
