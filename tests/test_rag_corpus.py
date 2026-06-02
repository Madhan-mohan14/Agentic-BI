"""
Verify RAG corpus write + read cycle.

Run: python tests/test_rag_corpus.py
Success: prints UPLOADED + at least 1 retrieved result.
"""

import os
import sys
import tempfile
import uuid

from dotenv import load_dotenv

load_dotenv()

project = os.environ.get("GOOGLE_CLOUD_PROJECT")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
corpus_id = os.environ.get("RAG_CORPUS_ID")

if not corpus_id:
    print("FAIL: RAG_CORPUS_ID not set in .env")
    sys.exit(1)

import vertexai
from vertexai import rag

vertexai.init(project=project, location=location)

# Step 1: Upload a test document
content = (
    "Query: what are the KPIs for last 2 days\n\n"
    "Approved Analysis:\nRevenue $66,191, Orders 716, AOV $92.45\n\n"
    "Score: 0.95\n"
)

with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write(content)
    tmp_path = f.name

try:
    rag_file = rag.upload_file(
        corpus_name=corpus_id,
        path=tmp_path,
        display_name=f"test_{uuid.uuid4().hex[:6]}.txt",
        description="RAG verification test",
    )
    print(f"UPLOADED: {rag_file.name}")
finally:
    os.remove(tmp_path)

# Step 2: Retrieve it
response = rag.retrieval_query(
    text="KPIs for last 2 days",
    rag_resources=[rag.RagResource(rag_corpus=corpus_id)],
    rag_retrieval_config=rag.RagRetrievalConfig(top_k=3),
)

count = len(response.contexts.contexts)
print(f"RETRIEVED {count} result(s)")

if count == 0:
    print("FAIL: nothing retrieved — corpus may need a few seconds to index. Try again in 30s.")
    sys.exit(1)

for ctx in response.contexts.contexts:
    print(f"  -> {ctx.text[:150]}")

print("\nRAG corpus: OK")
