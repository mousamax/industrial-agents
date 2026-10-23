"""Tracing: one OpenTelemetry pipeline, two optional sinks.

Logfire's SDK does the instrumentation (pydantic-ai, httpx, FastAPI). Spans go to:
- Logfire, if LOGFIRE_TOKEN or .logfire/logfire_credentials.json is set (best UI for pydantic-ai + pydantic-evals),
- Langfuse, if LANGFUSE_* keys are set (OTLP export; prompt/session views),
- nowhere, otherwise. Spans are still created, so pydantic-evals can read tool calls from them.

Call `setup()` once per process (CLI, API, tests all do).
"""

from __future__ import annotations

import base64
from functools import cache

import logfire

from app.settings import ROOT, get_settings


@cache
def setup(service_name: str = "industrial-agents") -> None:
    s = get_settings()
    processors = []
    if s.langfuse_public_key and s.langfuse_secret_key:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        auth = base64.b64encode(f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{s.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
        processors.append(BatchSpanProcessor(exporter))

    logfire.configure(
        service_name=service_name,
        environment=s.app_env,
        token=s.logfire_token,
        # Falls back to `.logfire/logfire_credentials.json` (from `logfire init`) when no token is set,
        # pinned to the repo root so it works from any working directory.
        data_dir=ROOT / ".logfire",
        send_to_logfire="if-token-present",
        additional_span_processors=processors,
        resource_attributes={"dev.name": s.dev_name} if s.dev_name else None,
        console=False,
        # Prompts and documents are the thing we debug; don't redact them.
        scrubbing=False,
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx()
