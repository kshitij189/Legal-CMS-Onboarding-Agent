"""OpenTelemetry spans and log/trace correlation."""

import json
import logging
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.integrations.mock_cms import MockCMSAdapter
from app.integrations.secrets import Credentials
from app.observability.logging import JsonFormatter
from tests.integration.conftest import setup_firm
from tests.unit.test_mock_cms_adapter import SCHEMA_BODY


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), "create_app() must install the SDK provider"
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()


def test_api_request_produces_server_and_database_spans(
    client: TestClient, spans: InMemorySpanExporter
) -> None:
    ctx = setup_firm(client)
    spans.clear()
    response = client.get(f"/api/v1/onboarding-projects/{ctx.project_id}", headers=ctx.admin)
    assert response.status_code == 200
    finished = spans.get_finished_spans()
    server = [s for s in finished if s.kind is trace.SpanKind.SERVER]
    assert server, [s.name for s in finished]
    assert server[0].attributes is not None
    assert server[0].attributes.get("http.route") == "/api/v1/onboarding-projects/{project_id}"
    db_spans = [s for s in finished if s.attributes and "db.statement" in s.attributes]
    assert db_spans, "SQLAlchemy queries must be traced"
    # Database spans belong to the same trace as the request.
    assert {s.context.trace_id for s in db_spans} == {server[0].context.trace_id}
    # Bound parameter values are not recorded with the statement.
    assert all("firm-a" not in str(s.attributes.get("db.statement")) for s in db_spans)  # type: ignore[union-attr]


class _FakeCMSHandler(BaseHTTPRequestHandler):
    """Minimal real HTTP server: httpx instrumentation only wraps the real network transport."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reply({"access_token": "tok", "token_type": "bearer"})

    def do_GET(self) -> None:  # noqa: N802
        self._reply(SCHEMA_BODY)

    def _reply(self, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


def test_outgoing_cms_calls_are_traced(spans: InMemorySpanExporter) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCMSHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        adapter = MockCMSAdapter(
            base_url, Credentials("a.integration", "pw"), frozenset({base_url})
        )
        spans.clear()
        adapter.inspect_target()
    finally:
        server.shutdown()
    client_spans = [s for s in spans.get_finished_spans() if s.kind is trace.SpanKind.CLIENT]
    methods = sorted(str((s.attributes or {}).get("http.method")) for s in client_spans)
    assert methods == ["GET", "POST"], [s.attributes for s in client_spans]
    # The password and token are never span attributes.
    for span in client_spans:
        assert "pw" not in json.dumps(dict(span.attributes or {}))


def test_logs_carry_trace_id_inside_a_span() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    assert json.loads(JsonFormatter().format(record))["trace_id"] is None
    with trace.get_tracer("test").start_as_current_span("unit") as span:
        entry = json.loads(JsonFormatter().format(record))
    assert entry["trace_id"] == format(span.get_span_context().trace_id, "032x")
