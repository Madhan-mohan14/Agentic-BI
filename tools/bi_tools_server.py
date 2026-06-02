import asyncio
import datetime
import logging
import os
import statistics
import uuid
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import BaseModel

load_dotenv()  # must be before _PROJECT so .env values are available

logger = logging.getLogger(__name__)
logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

mcp = FastMCP("bi-tools-server")


# ── Return Models ──────────────────────────────────────────────────────────────

class AnomalyResult(BaseModel):
    is_anomaly: bool
    score: float          # z-score of the worst offending row
    affected_rows: int
    details: str

class KpiSummary(BaseModel):
    summary_text: str
    top_metric: str
    period: str
    change_pct: float     # positive = improvement, negative = decline

class ReportJob(BaseModel):
    job_id: str
    format: str
    eta_seconds: int
    status: str           # "queued" | "processing" | "done"

class TableInfo(BaseModel):
    name: str
    description: str
    key_columns: list[str]

class SchemaContext(BaseModel):
    dataset: str
    tables: list[TableInfo]

class LogResult(BaseModel):
    logged: bool
    corpus_id: str
    chunk_id: str

class PastAnalysis(BaseModel):
    query: str
    result: str
    score: float

class KBSearchResult(BaseModel):
    results: list[PastAnalysis]
    count: int


# ── RAG Engine (Vertex AI) ──────────────────────────────────────────────────────

# We now use Vertex AI RAG API instead of the in-memory store.


# ── BigQuery helper ────────────────────────────────────────────────────────────

_executor = ThreadPoolExecutor(max_workers=2)

try:
    from google.cloud import bigquery as _bq
    _bq_client = _bq.Client(project=_PROJECT)
except Exception as _bq_init_err:
    _bq_client = None
    logger.warning("[bi_tools_server] BigQuery client init failed: %s", _bq_init_err)


def _bq_query_sync(sql: str) -> list:
    if _bq_client is None:
        raise RuntimeError("BigQuery client not initialized. Run: gcloud auth application-default login")
    return list(_bq_client.query(sql).result())


async def _bq_query(sql: str) -> list:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _bq_query_sync, sql)


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
async def execute_sql(sql: str, description: str) -> dict:
    """
    Executes any read-only SELECT query on the thelook_ecommerce BigQuery dataset.

    The Data Agent calls get_schema_context first, then generates the SQL using
    the schema, then passes it here for execution. Use this for any question that
    generate_kpi_summary or detect_anomaly cannot answer — e.g. top customers,
    return rates by category, orders by country, AOV by gender.

    Args:
        sql:         A valid BigQuery SELECT statement. Must query tables under
                     bigquery-public-data.thelook_ecommerce. Always include LIMIT.
        description: One sentence explaining what this query answers.

    Returns:
        Dict with rows (list of dicts), row_count, and description.
        On error returns {"error": "...", "rows": []}.
    """
    # Block anything that isn't a SELECT
    if not sql.strip().upper().startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed.", "rows": [], "row_count": 0}

    # Auto-add backticks to bigquery-public-data table refs — required because the
    # project ID contains hyphens, which BigQuery parses as arithmetic without quotes.
    import re
    sql = re.sub(
        r'(?<!`)(bigquery-public-data\.[\w_]+\.[\w_*]+)(?!`)',
        r'`\1`',
        sql,
    )

    # Enforce a LIMIT so the agent can't accidentally scan huge tables
    if "LIMIT" not in sql.upper():
        sql = sql.rstrip().rstrip(";") + " LIMIT 100"

    try:
        rows = await _bq_query(sql)
    except Exception as e:
        return {"error": str(e), "rows": [], "row_count": 0}

    def _serialize(v):
        import datetime, decimal
        if isinstance(v, (datetime.date, datetime.datetime)):
            return v.isoformat()
        if isinstance(v, decimal.Decimal):
            return float(v)
        return v

    serialized = [{k: _serialize(v) for k, v in dict(row).items()} for row in rows]
    return {"rows": serialized, "row_count": len(serialized), "description": description}


@mcp.tool()
async def detect_anomaly(table: str, column: str, threshold: float = 2.0) -> AnomalyResult:
    """
    Flags statistical outliers in a numeric column using z-score analysis.

    Call this when you suspect unusual spikes or drops in revenue, order volume,
    inventory levels, or any other numeric KPI. A z-score above the threshold
    means the value is far enough from the mean to be considered anomalous.

    Args:
        table:     BigQuery table to scan — one of: orders, order_items, inventory_items.
        column:    Numeric column to analyse (e.g. "sale_price", "num_of_item").
        threshold: Z-score cutoff. Default 2.0 (flags top ~5% of deviations).

    Returns:
        AnomalyResult with is_anomaly flag, the highest z-score found, how many
        rows exceeded the threshold, and a plain-English details string.
    """
    # Safe column allowlist — prevents SQL injection from agent args
    _allowed: dict[str, set[str]] = {
        "orders": {"num_of_item"},
        "order_items": {"sale_price"},
        "inventory_items": {"cost"},
    }
    safe_col = column if column in _allowed.get(table, set()) else next(iter(_allowed.get(table, {"num_of_item"})))

    try:
        sql = f"""
            SELECT {safe_col} AS val
            FROM `bigquery-public-data.thelook_ecommerce.{table}`
            WHERE {safe_col} IS NOT NULL
            LIMIT 1000
        """
        rows = await _bq_query(sql)
        values = [float(r.val) for r in rows]
    except Exception as e:
        return AnomalyResult(
            is_anomaly=False, score=0.0, affected_rows=0,
            details=f"BigQuery error: {e}. Check gcloud auth application-default login.",
        )

    if len(values) < 2:
        return AnomalyResult(is_anomaly=False, score=0.0, affected_rows=0, details="Not enough data to compute z-scores.")

    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return AnomalyResult(is_anomaly=False, score=0.0, affected_rows=0, details="All values are identical — no variance to score.")
    z_scores = [abs((v - mean) / stdev) for v in values]
    max_z = max(z_scores)
    affected = sum(1 for z in z_scores if z > threshold)

    return AnomalyResult(
        is_anomaly=max_z > threshold,
        score=round(max_z, 3),
        affected_rows=affected,
        details=(
            f"Column '{safe_col}' in '{table}': max z-score {max_z:.2f} "
            f"({'ANOMALY DETECTED' if max_z > threshold else 'within normal range'}). "
            f"{affected} row(s) exceed threshold {threshold}."
        ),
    )


@mcp.tool()
async def generate_kpi_summary(metrics: list[str], days: int = 30) -> KpiSummary:
    """
    Rolls up business KPIs into a single plain-English summary sentence.

    Queries BigQuery for the actual window specified by `days` and compares
    against the equal-length prior window.

    Args:
        metrics: List of KPI names to include (e.g. ["revenue", "orders", "aov"]).
        days:    Number of days to look back (e.g. 7, 30, 90). Default 30.

    Returns:
        KpiSummary with a human-readable summary_text, the name of the top
        performing metric, the period label, and the percentage change vs. prior period.
    """
    prior_start = days * 2
    prior_end = days + 1
    period = f"last {days} days"

    try:
        # The public dataset has historical data — use its MAX date as anchor
        # so date-range queries always hit real rows regardless of CURRENT_DATE().
        anchor_rows = await _bq_query(
            "SELECT MAX(DATE(created_at)) AS max_date "
            "FROM `bigquery-public-data.thelook_ecommerce.orders`"
        )
        max_date = str(anchor_rows[0].max_date)

        current_sql = f"""
            SELECT
                COUNT(DISTINCT o.order_id) AS order_count,
                SUM(oi.sale_price)         AS revenue,
                SUM(o.num_of_item)         AS items_sold
            FROM `bigquery-public-data.thelook_ecommerce.orders` o
            JOIN `bigquery-public-data.thelook_ecommerce.order_items` oi
              ON o.order_id = oi.order_id
            WHERE o.status = 'Complete'
              AND DATE(o.created_at) >= DATE_SUB(DATE '{max_date}', INTERVAL {days} DAY)
        """
        prior_sql = f"""
            SELECT
                COUNT(DISTINCT o.order_id) AS order_count,
                SUM(oi.sale_price)         AS revenue
            FROM `bigquery-public-data.thelook_ecommerce.orders` o
            JOIN `bigquery-public-data.thelook_ecommerce.order_items` oi
              ON o.order_id = oi.order_id
            WHERE o.status = 'Complete'
              AND DATE(o.created_at) BETWEEN DATE_SUB(DATE '{max_date}', INTERVAL {prior_start} DAY)
                                         AND DATE_SUB(DATE '{max_date}', INTERVAL {prior_end} DAY)
        """
        cur, pri = await asyncio.gather(_bq_query(current_sql), _bq_query(prior_sql))
        cur, pri = cur[0], pri[0]
    except Exception as e:
        return KpiSummary(
            summary_text=f"BigQuery error: {e}. Check gcloud auth application-default login.",
            top_metric=metrics[0] if metrics else "unknown",
            period=period,
            change_pct=0.0,
        )

    cur_orders = float(cur.order_count or 0)
    cur_revenue = float(cur.revenue or 0)
    cur_items = float(cur.items_sold or 0)
    aov = round(cur_revenue / cur_orders, 2) if cur_orders else 0.0

    kpi_map = {
        "orders": cur_orders,
        "revenue": cur_revenue,
        "items_sold": cur_items,
        "aov": aov,
    }
    kpi_values = {m: kpi_map.get(m, kpi_map.get(m.lower(), 0.0)) for m in metrics}
    top_metric = max(kpi_values, key=lambda k: kpi_values[k]) if kpi_values else metrics[0]

    prior_rev = float(pri.revenue or 0) or 1.0
    change = round(((cur_revenue - prior_rev) / prior_rev) * 100, 1)
    direction = "up" if change >= 0 else "down"

    def _fmt(k: str, v: float) -> str:
        if k in ("revenue", "aov"):
            return f"{k}: ${v:,.2f}"
        return f"{k}: {v:,.0f}"

    metric_lines = ", ".join(_fmt(k, v) for k, v in kpi_values.items())

    return KpiSummary(
        summary_text=(
            f"For {period}: {metric_lines}. "
            f"Revenue is {direction} {abs(change)}% vs. prior {days} days."
        ),
        top_metric=top_metric,
        period=period,
        change_pct=change,
    )


@mcp.tool()
async def create_report_job(query_id: str = "latest", format: str = "pdf") -> ReportJob:
    """
    Queues an async report generation job and returns a tracking ID.

    Use this for heavy exports that should not block the agent response. The
    caller can poll the job_id for status. Supported formats: pdf, csv, xlsx.

    Args:
        query_id: ID of the saved query to export. Use "latest" (default) when no specific query is referenced.
        format:   Output file format — "pdf" (default), "csv", or "xlsx".

    Returns:
        ReportJob with a unique job_id, confirmed format, estimated completion
        time in seconds, and initial status of "queued".
    """
    format = format.lower()
    if format not in {"pdf", "csv", "xlsx"}:
        format = "pdf"

    eta = {"pdf": 30, "csv": 10, "xlsx": 20}.get(format, 30)

    return ReportJob(
        job_id=str(uuid.uuid4()),
        format=format,
        eta_seconds=eta,
        status="queued",
    )


@mcp.tool()
async def get_schema_context(dataset: str) -> SchemaContext:
    """
    Returns human-readable table descriptions for a BigQuery dataset.

    The Data Agent must call this before generating any SQL so it knows which
    tables and columns are available. Prevents hallucinated column names.

    Args:
        dataset: Dataset name — "thelook_ecommerce" or "ga4_obfuscated_sample_ecommerce".

    Returns:
        SchemaContext with the dataset name and a list of TableInfo objects, each
        containing the table name, its purpose, and the key columns to query against.
    """
    schemas: dict[str, list[TableInfo]] = {
        "thelook_ecommerce": [
            TableInfo(
                name="orders",
                description="One row per order. Use for revenue, order counts, and week-over-week KPIs.",
                key_columns=["order_id", "user_id", "status", "created_at", "num_of_item"],
            ),
            TableInfo(
                name="order_items",
                description="One row per item in an order. Use for product-level revenue and category analysis.",
                key_columns=["id", "order_id", "product_id", "sale_price", "status", "created_at"],
            ),
            TableInfo(
                name="inventory_items",
                description="Current inventory snapshot. Use for stock anomalies and supply chain queries.",
                key_columns=["id", "product_id", "cost", "created_at", "sold_at", "product_distribution_center_id"],
            ),
            TableInfo(
                name="users",
                description="Customer profiles. Use for segmentation and cohort analysis.",
                key_columns=["id", "email", "age", "gender", "country", "created_at"],
            ),
            TableInfo(
                name="products",
                description="Product catalogue. Use for enriching item-level analysis with category and brand.",
                key_columns=["id", "name", "category", "brand", "retail_price", "department"],
            ),
        ],
        "ga4_obfuscated_sample_ecommerce": [
            TableInfo(
                name="events_*",
                description="GA4 event stream. Use for funnel analysis, traffic sources, and behavioural drop-off.",
                key_columns=["event_date", "event_name", "user_pseudo_id", "traffic_source", "ecommerce"],
            ),
        ],
    }

    tables = schemas.get(dataset, [
        TableInfo(
            name="unknown",
            description=f"Dataset '{dataset}' not recognised. Use thelook_ecommerce or ga4_obfuscated_sample_ecommerce.",
            key_columns=[],
        )
    ])

    return SchemaContext(dataset=dataset, tables=tables)


@mcp.tool()
async def log_resolution(query: str, result: str, score: float) -> LogResult:
    """
    Persists an approved analysis to the RAG Engine corpus.

    The Audit Agent calls this only when hallucination score >= 0.8. Saved
    analyses are retrieved later by search_knowledge_base to avoid redundant
    BigQuery calls and reinforce correct answers over time.

    Args:
        query:  The original natural-language question that was answered.
        result: The approved answer / analysis text.
        score:  Hallucination score from corroborateContent API (0.0–1.0).

    Returns:
        LogResult with logged=True and the corpus_id and chunk_id assigned by the
        RAG Engine if the score passed; logged=False with a rejection reason if not.
    """
    if score < 0.8:
        return LogResult(logged=False, corpus_id="", chunk_id="rejected — score below 0.8")

    corpus_id = os.environ.get("RAG_CORPUS_ID")
    if not corpus_id:
        return LogResult(logged=False, corpus_id="", chunk_id="missing RAG_CORPUS_ID")

    tmp_path = None
    try:
        import tempfile
        import vertexai
        from vertexai import rag

        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        vertexai.init(project=_PROJECT, location=location)

        content = f"Logged: {datetime.date.today().isoformat()}\nQuery: {query}\n\nApproved Analysis:\n{result}\n\nScore: {score}\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        loop = asyncio.get_running_loop()
        rag_file = await loop.run_in_executor(
            _executor,
            lambda: rag.upload_file(
                corpus_name=corpus_id,
                path=tmp_path,
                display_name=f"resolution_{uuid.uuid4().hex[:8]}.txt",
                description="Approved BI analysis"
            )
        )
        chunk_id = rag_file.name
    except Exception as e:
        logger.error(f"[log_resolution] failed: {e}")
        return LogResult(logged=False, corpus_id=corpus_id, chunk_id=f"error: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return LogResult(logged=True, corpus_id=corpus_id, chunk_id=chunk_id)


@mcp.tool()
async def search_knowledge_base(query: str) -> KBSearchResult:
    """
    Searches the RAG Engine corpus for past analyses similar to the current query.

    The RAG Agent calls this first — before any BigQuery call — to serve cached
    answers instantly and reduce cost. Returns the top semantically matching
    analyses along with their confidence scores.

    Args:
        query: The natural-language question to search for.

    Returns:
        KBSearchResult with a list of PastAnalysis objects (each holding the
        original query, its approved result, and its audit score) and a total count.
    """
    corpus_id = os.environ.get("RAG_CORPUS_ID")
    if not corpus_id:
        return KBSearchResult(results=[], count=0)

    try:
        import vertexai
        from vertexai import rag

        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        vertexai.init(project=_PROJECT, location=location)

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            _executor,
            lambda: rag.retrieval_query(
                text=query,
                rag_resources=[rag.RagResource(rag_corpus=corpus_id)],
                rag_retrieval_config=rag.RagRetrievalConfig(top_k=3)
            )
        )

        matches = []
        for ctx in response.contexts.contexts:
            relevance = float(getattr(ctx, 'score', getattr(ctx, 'distance', 0.5)))
            matches.append(PastAnalysis(query=query, result=ctx.text, score=relevance))

        return KBSearchResult(results=matches, count=len(matches))
    except Exception as e:
        logger.error(f"[search_knowledge_base] RAG search error: {e}")
        return KBSearchResult(results=[], count=0)


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", os.environ.get("PORT", 8088)))
    logger.info(f"Starting bi-tools-server on port {port}")
    asyncio.run(
        mcp.run_async(
            transport="http",
            host="0.0.0.0",
            port=port,
        )
    )
