import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools import google_search
from google.genai import types

load_dotenv()

research_agent = LlmAgent(
    name="research_agent",
    model="gemini-2.5-flash",
    description=(
        "Research agent for explaining WHY business trends happen. "
        "Use for: why revenue dropped, what caused an anomaly, market context, external causes. "
        "Do NOT use for BigQuery data or KPI numbers."
    ),
    instruction=(
        """You are the research agent for an ecommerce BI system. You explain WHY business trends happen using live web search.

Call google_search with a focused query about the business topic asked.
Synthesize the results into a clear explanation of the likely causes — be specific, not generic.
If search returns nothing useful, answer from your training knowledge.

Write your complete analysis and stop. The orchestrator handles what happens next."""
    ),
    tools=[google_search],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
        top_p=0.95,
        max_output_tokens=2048,
    ),
    output_key="research_result",
)
