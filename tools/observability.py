import os
import base64

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
try:
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    _HAS_OPENINFERENCE = True
except ImportError:
    _HAS_OPENINFERENCE = False


_tracing_initialized = False


def setup_tracing(service_name: str = "agentic-bi-system") -> None:
    """
    Wire OpenTelemetry → Langfuse.

    Call once at app startup before any agent runs. Skips silently if
    LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY are not set (e.g. local dev
    without a Langfuse account).

    Args:
        service_name: Label shown in Langfuse trace list. Default "agentic-bi-system".

    Returns:
        None. Tracing is configured globally via the OTel TracerProvider.
    """
    global _tracing_initialized
    if _tracing_initialized:
        return
    _tracing_initialized = True

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        return

    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    exporter = OTLPSpanExporter(
        endpoint=f"{host}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {credentials}"},
    )

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    if _HAS_OPENINFERENCE:
        instrumentor = GoogleADKInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument()

    print(f"Tracing active -> {host}")
