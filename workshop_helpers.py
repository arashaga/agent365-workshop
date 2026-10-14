"""Shared settings, model connection, and activity display for the guided workshop."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from azure.identity.aio import AzureCliCredential, get_bearer_token_provider
from dotenv import load_dotenv
from IPython.display import HTML, display
from openai import AsyncAzureOpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

recorded_spans = InMemorySpanExporter()
workshop_span_processor = SimpleSpanProcessor(recorded_spans)


def required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in Agent_365/.env, then restart the kernel.")
    return value


def prepare_workshop() -> str:
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    # Keep optional SDK-health reporting separate from the Agent 365 workshop.
    os.environ["MICROSOFT_OTEL_SDKSTATS_DISABLED"] = "true"
    endpoint = urlsplit(required_setting("AZURE_OPENAI_ENDPOINT"))
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username:
        raise ValueError("AZURE_OPENAI_ENDPOINT must be an HTTPS service URL without credentials.")
    return required_setting("AZURE_OPENAI_DEPLOYMENT")


@asynccontextmanager
async def connect_model() -> AsyncIterator[AsyncAzureOpenAI]:
    async with (
        AzureCliCredential(tenant_id=os.getenv("A365_TENANT_ID") or None) as credential,
        AsyncAzureOpenAI(
            azure_endpoint=required_setting("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            azure_ad_token_provider=get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            ),
            timeout=60,
            max_retries=1,
        ) as client,
    ):
        yield client


def require_fresh_tracing() -> None:
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        raise RuntimeError("Restart the kernel before configuring workshop tracing.")
    if any(name.startswith("OTEL_EXPORTER_OTLP") and value for name, value in os.environ.items()):
        raise RuntimeError(
            "Unset OTEL_EXPORTER_OTLP* so this workshop uses only its configured destinations."
        )
    for name in ("ENABLE_OBSERVABILITY", "ENABLE_A365_OBSERVABILITY"):
        if os.getenv(name, "").lower() in ("false", "0", "no", "off"):
            raise RuntimeError(f"Remove the {name} override before this workshop.")


def start_recording() -> None:
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        raise RuntimeError(
            "Run the notebook's tracing setup cell first. No local fallback is configured."
        )
    recorded_spans.clear()


def show_table(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("No items were returned.")
        return
    columns = list(rows[0])
    heading = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{escape(str(row.get(column, '-')))}</td>" for column in columns)
        + "</tr>"
        for row in rows
    )
    display(HTML(f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"))


def show_activities() -> None:
    spans = recorded_spans.get_finished_spans()
    if not spans:
        print("No activities recorded yet. Run the example just above this cell.")
        return
    show_table(
        [
            {
                "Activity": span.name,
                "Seconds": round((span.end_time - span.start_time) / 1_000_000_000, 2),
                "Tokens sent": (span.attributes or {}).get("gen_ai.usage.input_tokens", "-"),
                "Tokens received": (span.attributes or {}).get("gen_ai.usage.output_tokens", "-"),
                "Result": "Error"
                if span.status.status_code == trace.StatusCode.ERROR
                else "Finished",
            }
            for span in sorted(spans, key=lambda item: item.start_time)
        ]
    )
