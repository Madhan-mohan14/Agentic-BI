import argparse
import datetime
import json
import logging
import os
import sys

import vertexai
from dotenv import load_dotenv, set_key
from google.cloud import storage
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

load_dotenv(ENV_FILE, override=True)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from orchestrator.agent import root_agent


def load_requirements() -> list[str]:
    """Read requirements.txt for Agent Engine packaging (full stack)."""
    req_path = os.path.join(PROJECT_ROOT, "requirements.txt")
    reqs = []
    with open(req_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                reqs.append(line)
    return reqs


def setup_staging_bucket(project_id: str, location: str, bucket_name: str) -> str:
    """Creates the GCS staging bucket if it does not already exist."""
    client = storage.Client(project=project_id)
    name = bucket_name.replace("gs://", "")
    try:
        bucket = client.lookup_bucket(name)
        if bucket:
            logger.info("Staging bucket gs://%s already exists.", name)
        else:
            logger.info("Creating staging bucket gs://%s ...", name)
            client.create_bucket(name, project=project_id, location=location)
            logger.info("Created gs://%s.", name)
    except Exception as exc:
        logger.error("Failed to access/create gs://%s: %s", name, exc)
        raise
    return f"gs://{name}"


def write_deployment_metadata(remote_agent, metadata_file: str = "deployment_metadata.json") -> None:
    metadata = {
        "resource_name": remote_agent.resource_name,
        "deployment_target": "agent_engine",
        "is_a2a": False,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    path = os.path.join(PROJECT_ROOT, metadata_file)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Deployment metadata written to %s", path)


def update_env_file(agent_engine_id: str) -> None:
    set_key(ENV_FILE, "AGENT_ENGINE_ID", agent_engine_id)
    logger.info("AGENT_ENGINE_ID written to .env: %s", agent_engine_id)


def build_env_vars() -> dict[str, str]:
    """Runtime env vars for Agent Engine — GOOGLE_CLOUD_PROJECT/LOCATION are reserved, excluded."""
    env_vars = {
        "GOOGLE_GENAI_USE_VERTEXAI": "True",
        "MCP_URL": str(os.getenv("MCP_URL", "")),
        "AUDIT_A2A_URL": str(os.getenv("AUDIT_A2A_URL", "")),
    }
    for key in ("RAG_CORPUS_ID", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        val = os.getenv(key)
        if val:
            env_vars[key] = val
    return env_vars


def main(mode: str) -> None:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    if not location or location == "global":
        location = "us-central1"

    bucket_raw = os.getenv("GCP_STAGING_BUCKET", "").replace("gs://", "")
    if not bucket_raw:
        bucket_raw = f"{project}-agentic-bi-staging"

    staging_bucket = setup_staging_bucket(project, location, bucket_raw)
    vertexai.init(project=project, location=location, staging_bucket=staging_bucket)

    env_vars = build_env_vars()
    logger.info("env_vars keys: %s", list(env_vars.keys()))

    adk_app = AdkApp(agent=root_agent, enable_tracing=True)
    requirements = load_requirements()
    extra_packages = ["./orchestrator", "./sub_agents", "./tools"]

    if mode == "create":
        logger.info("Creating Agent Engine deployment ...")
        remote_agent = agent_engines.create(
            adk_app,
            display_name="agentic-bi",
            requirements=requirements,
            extra_packages=extra_packages,
            env_vars=env_vars,
        )
        logger.info("Deployed: %s", remote_agent.resource_name)
        update_env_file(remote_agent.resource_name)
        write_deployment_metadata(remote_agent)

    elif mode == "update":
        agent_engine_id = os.getenv("AGENT_ENGINE_ID")
        if not agent_engine_id:
            logger.error("AGENT_ENGINE_ID not set in .env — run --mode create first.")
            sys.exit(1)
        logger.info("Updating Agent Engine %s ...", agent_engine_id)
        remote_agent = agent_engines.get(agent_engine_id)
        remote_agent.update(
            agent_engine=adk_app,
            display_name="agentic-bi",
            requirements=requirements,
            extra_packages=extra_packages,
            env_vars=env_vars,
        )
        logger.info("Updated: %s", remote_agent.resource_name)
        write_deployment_metadata(remote_agent)

    else:
        logger.error("Invalid mode '%s'. Use --mode create or --mode update.", mode)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy Agentic BI orchestrator to Agent Engine")
    parser.add_argument(
        "--mode",
        type=str,
        default="create",
        choices=["create", "update"],
        help="'create' for first deploy, 'update' for subsequent deploys",
    )
    args = parser.parse_args()
    main(args.mode)
