import os
import sys

import vertexai
from dotenv import load_dotenv, set_key
from vertexai.preview import rag

load_dotenv()

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env")

if not PROJECT:
    print("ERROR: GOOGLE_CLOUD_PROJECT not set in .env")
    sys.exit(1)

vertexai.init(project=PROJECT, location=LOCATION)

print(f"Creating RAG corpus in project={PROJECT}, location={LOCATION} ...")
corpus = rag.create_corpus(display_name="agentic-bi-knowledge-base")
corpus_name = corpus.name
print(f"Created: {corpus_name}")

set_key(os.path.abspath(ENV_FILE), "RAG_CORPUS_ID", corpus_name)
print(f"RAG_CORPUS_ID written to .env")
print()
print("Next: restart bi_tools_server so it picks up RAG_CORPUS_ID.")
print("Tools 6 (log_resolution) and 7 (search_knowledge_base) will now use the corpus.")
