"""
Agent Registry validation script (GOVERN pillar).

Reads deployment/agent_registry.yaml, expands env vars in URLs,
and prints a status table showing which services are reachable.
Run after Agent Engine deployment to verify registry completeness.
"""

import os
import sys

import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

REGISTRY_FILE = os.path.join(os.path.dirname(__file__), "agent_registry.yaml")


def _expand(value: str) -> str:
    """Expand ${VAR} placeholders using environment variables."""
    import re
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), f"<{m.group(1)}-not-set>"), value)


def check_url(name: str, url: str) -> str:
    if "<" in url:
        return "NOT SET"
    try:
        resp = httpx.get(url, timeout=5)
        return f"OK ({resp.status_code})"
    except Exception as exc:
        return f"UNREACHABLE ({exc})"


def main() -> None:
    with open(REGISTRY_FILE, encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    print(f"\n=== Agent Registry: {registry['registry']['name']} ===\n")
    print(f"{'Name':<20} {'Type':<14} {'URL':<60} {'Status'}")
    print("-" * 110)

    for agent in registry["registry"]["agents"]:
        url = _expand(agent["url"])
        status = check_url(agent["name"], url) if not url.startswith("agentengine") else "Agent Engine (skip HTTP check)"
        print(f"{agent['name']:<20} {agent['type']:<14} {url:<60} {status}")

    print(f"\n{'Tool':<30} {'Server':<20} {'Layer'}")
    print("-" * 60)
    for tool in registry["registry"]["tools"]:
        print(f"{tool['name']:<30} {tool['server']:<20} {tool['layer']}")

    print()


if __name__ == "__main__":
    main()
