"""Azure operational tracing, optionally alongside the Agent 365 exporter."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from agent_framework.observability import enable_instrumentation
from azure.identity import AzureCliCredential
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from .agent365_auth import BlueprintTokenResolver
from .workshop_helpers import (
    recorded_spans,
    require_fresh_tracing,
    required_setting,
    workshop_span_processor,
)


def configure_workshop_tracing(
    tenant_id: str,
    agent_id: str,
    *,
    token_resolver: BlueprintTokenResolver | None = None,
) -> None:
    require_fresh_tracing()
    tenant_id, agent_id = str(UUID(tenant_id)), str(UUID(agent_id))
    if token_resolver is not None and (
        token_resolver.tenant_id != tenant_id or token_resolver.agent_id != agent_id
    ):
        raise ValueError("The telemetry credential must belong to this tenant and agent.")
    connection_string = required_setting("APPLICATIONINSIGHTS_CONNECTION_STRING")
    os.environ["MICROSOFT_OTEL_SDKSTATS_DISABLED"] = "true"
    azure_processor = BatchSpanProcessor(
        AzureMonitorTraceExporter(
            connection_string=connection_string,
            credential=AzureCliCredential(tenant_id=tenant_id),
            disable_offline_storage=True,
        )
    )
    resource = Resource.create(
        {
            "service.name": "agent365-identity-workshop",
            "gen_ai.agent.id": agent_id,
            "microsoft.tenant.id": tenant_id,
        }
    )
    if token_resolver is None:
        provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
        provider.add_span_processor(workshop_span_processor)
        provider.add_span_processor(azure_processor)
        trace.set_tracer_provider(provider)
    else:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        libraries = ("openai", "openai_agents", "agent_framework")
        use_microsoft_opentelemetry(
            enable_a365=True,
            enable_azure_monitor=False,
            a365_token_resolver=token_resolver,
            a365_use_s2s_endpoint=True,
            a365_enable_observability_exporter=True,
            a365_suppress_invoke_agent_input=True,
            enable_sensitive_data=False,
            enable_console=False,
            disable_metrics=True,
            disable_logging=True,
            resource=resource,
            span_processors=[workshop_span_processor, azure_processor],
            instrumentation_options={name: {"enabled": False} for name in libraries},
        )
    enable_instrumentation(enable_sensitive_data=False)


async def flush_workshop_trace() -> str:
    spans = recorded_spans.get_finished_spans()
    roots = [
        span
        for span in spans
        if span.parent is None
        and (span.attributes or {}).get("gen_ai.operation.name") == "invoke_agent"
    ]
    if len(roots) != 1:
        raise RuntimeError("Record exactly one agent run after start_recording(), then flush.")
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        raise RuntimeError("Configure workshop tracing before flushing.")
    if not await asyncio.to_thread(provider.force_flush, timeout_millis=30_000):
        raise RuntimeError("Trace export timed out. Check the exporter errors.")
    trace_id = format(roots[0].context.trace_id, "032x")
    print("Export queue flushed; cloud ingestion is NOT yet verified.")
    print(f"Trace ID: {trace_id}")
    print("In Application Insights > Logs (KQL mode), run:")
    print(
        'union requests, dependencies\n'
        f'| where timestamp > ago(1h) and operation_Id == "{trace_id}"\n'
        '| project timestamp, itemType, name, id, operation_ParentId, success, duration\n'
        '| order by timestamp asc'
    )
    return trace_id
