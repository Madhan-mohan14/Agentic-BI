---
name: teaching-mode
description: >
  Delivers a structured learning recap after every completed build step in this project.
  This skill should trigger automatically after writing any new file, implementing any tool,
  getting any server running, or completing any significant code change. If the user asks
  "explain what you built", "teach me", "how does this work", or uses /teach — use this skill.
  Also trigger proactively after any build completion without being asked — the user is learning
  Agentic AI from scratch and expects a recap after every step.
---

# Teaching Mode

The user is learning Agentic AI from scratch. After every completed build step, deliver a learning recap unprompted. This is as important as the build itself — understanding what was built is the goal, not just having working code.

## When to Deliver a Recap
Deliver after:
- Writing a new Python file
- Implementing and testing a tool or agent
- Getting a server running (MCP, ADK, FastAPI)
- Any significant code change (callbacks, plugins, A2A wiring)

Don't wait to be asked. The recap is part of every build step.

## Recap Format

### What Was Built
One paragraph in plain English. What does this code do? What problem does it solve? Avoid jargon — if you use a technical term, define it in the same sentence.

### Key Code Decisions
For each major pattern (2–4 decisions max):
- **Decision**: what was chosen
- **Why**: the concrete reason, not "best practice"
- **Alternative**: what could have been done instead and why it was less suitable

Keep these tight — one short paragraph each. Focus on the decisions that would surprise a beginner.

### How the Pieces Connect
A short data flow description: what calls what, in what order, what data moves between them. Use arrows (`→`) to show flow. One sentence per step is enough.

### Try This Now
One specific, runnable action the user can take right now to see the code working or to explore it. Give the exact command or action — not "try running it", but `npx @modelcontextprotocol/inspector python tools/bi_tools_server.py`.

## Example Recap

**What was built:** `bi_tools_server.py` — a FastMCP server with `detect_anomaly` as the first tool. FastMCP is a Python library that wraps your functions and makes them callable by any AI agent over the MCP protocol (a standard like HTTP but for AI tools).

**Key decisions:**
- **`@mcp.tool()` decorator**: registers the function as an MCP tool AND auto-generates its JSON schema from the type hints. The alternative was writing the schema manually — more work, more error-prone.
- **Pydantic `AnomalyResult` return model**: gives the calling agent a structured, typed response. A plain dict would work too, but the agent wouldn't know the exact shape of the response ahead of time.
- **`async def`**: the tool is async because ADK agents are async — a sync tool would block the agent's event loop.

**Flow:** MCP Inspector → sends JSON-RPC call → FastMCP server → routes to `detect_anomaly()` → runs z-score logic → returns `AnomalyResult` as a structured JSON response.

**Try this now:** Run `npx @modelcontextprotocol/inspector python tools/bi_tools_server.py`, click Connect, select `detect_anomaly`, enter `{"table": "orders", "column": "revenue", "threshold": 2.0}`, click Call. You should see an `AnomalyResult` in the response panel.

## Calibration Notes
- The user knows Python basics but is new to Agentic AI patterns
- Explain protocols (MCP, A2A, ADK) as if they're new concepts
- Use analogies: "MCP is like a REST API but specifically for AI tools"
- Don't explain things that follow obviously from the code
- Keep the whole recap under 250 words — tight and scannable
