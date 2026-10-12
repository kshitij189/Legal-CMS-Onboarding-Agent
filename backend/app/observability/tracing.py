"""OpenTelemetry tracing (CLAUDE.md §13).

Instruments incoming API requests, SQLAlchemy queries and outgoing httpx calls to the CMS.
Export is chosen by OTEL_TRACES_EXPORTER: "none" (default; spans are still created, so trace ids
appear in logs), "console", or "otlp" (OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT, e.g. Jaeger).
Request/response bodies are never recorded; SQL is recorded without bound parameter values.
"""

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from sqlalchemy import Engine

EXPORTERS = ("none", "console", "otlp")


def configure_tracing(app: FastAPI, engine: Engine) -> TracerProvider:
    exporter = os.environ.get("OTEL_TRACES_EXPORTER", "none").lower()
    if exporter not in EXPORTERS:
        raise ValueError(f"OTEL_TRACES_EXPORTER must be one of {EXPORTERS}, got '{exporter}'.")
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.environ.get("OTEL_SERVICE_NAME", "onboarding-backend")}
        )
    )
    if exporter == "otlp":
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="healthz")
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    return provider


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
