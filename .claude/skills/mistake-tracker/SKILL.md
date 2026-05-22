---
name: mistake-tracker
description: >
  Logs mistakes immediately when they occur and checks the mistake log before starting tasks.
  Use this skill whenever: a command fails, code doesn't work as expected, a wrong pattern was used,
  or something needs to be undone. Also trigger proactively at the START of any task that resembles
  something previously logged (hardcoding, wrong model, sync instead of async, missing schema check).
  If the user says "that was wrong", "fix this mistake", "we did this wrong", or uses /mistake — use
  this skill immediately. Don't wait — log it while the context is fresh.
---

# Mistake Tracker

Mistakes are logged to stop them from happening twice. This project is a learning environment — every error is a lesson, not a problem. The log is the memory.

## On Mistake Discovery — Log It Immediately

Log to BOTH places in one pass:

**1. CLAUDE.md → Mistake Log table** (at end of file):
```markdown
| 2026-05-11 | What went wrong (specific) | Correct approach | When this triggers |
```

**2. Memory file** `C:/Users/madhanmohan/.claude/projects/D--Agentic-Business/memory/feedback_mistakes.md`:
```
[2026-05-11] MISTAKE: <what went wrong>
FIX: <correct approach>
AVOID: <trigger condition — what situation leads to this mistake>
```

Write the entry while the mistake is fresh. Vague entries like "forgot async" are less useful than "forgot async on `before_tool_callback` — ADK silently ignores sync callbacks without error".

## Before Starting Any Task

Scan the mistake log for entries with a matching AVOID condition. If one matches:
1. State it upfront: "This is similar to a past mistake — [entry]. Applying the fix proactively."
2. Use the correct approach from the start

This takes 5 seconds and prevents wasted iteration.

## Known Pitfalls for This Stack

These are pre-loaded from project knowledge — treat them as existing log entries:

| Mistake | Fix | Triggers When |
|---------|-----|--------------|
| `def before_tool_callback` (sync) | Must be `async def` | Writing any callback |
| `bigquery_execute_sql` without schema check | Always call `bigquery_get_schema` first | Data Agent work |
| `PROJECT_ID = "my-project"` hardcoded | Use `os.environ.get("GCP_PROJECT_ID")` | Any GCP resource |
| `return {}` from FastMCP tool (raw dict) | Use Pydantic model | Writing MCP tools |
| MCP Inspector before server starts | Start server, wait for "running" message, then Inspector | Day 1 testing |
| `gemini-2.5-flash` for main agents | `gemini-2.0-flash` for agents; 2.5-flash only for eval/audit | Agent construction |
| Flat `.md` skill file (no frontmatter) | Add YAML frontmatter with `name` + `description` | Writing new skills |

## After Logging

After adding the entry, briefly tell the user:
- What was logged
- The fix going forward
- Whether to continue or redo the previous step with the fix applied

Keep it short — one sentence each. The goal is to move forward, not dwell.
