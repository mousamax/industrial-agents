"""Tracing: Pydantic AI's built-in OpenTelemetry instrumentation, viewed in Langfuse.

What you get per run, with no code in the agents: one trace named after the agent/flow, with every
model call (model, prompt, tokens, cost), tool call (args, result) and nested agent, tagged with the
agent name and your DEV_NAME. Flows show as a `chain` node with their agents nested under it.

- LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL set: traces go to Langfuse
  (flushed automatically at exit).
- Not set: spans are only kept in memory, which the eval runner still uses to check tool calls.

Call `setup()` once per process before running agents (CLI, API, tests and evals do).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from functools import cache
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel
from pydantic_ai import Agent

from app.settings import get_settings


@cache
def langfuse_enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_public_key and s.langfuse_secret_key)


@cache
def setup() -> None:
    s = get_settings()  # loads .env into os.environ first, which the Langfuse client reads
    if langfuse_enabled():
        import os

        from langfuse import get_client

        os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", s.app_env)
        get_client()  # registers Langfuse as the OpenTelemetry tracer provider
    else:
        trace.set_tracer_provider(TracerProvider())
    Agent.instrument_all()


def _jsonable(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


@contextmanager
def entrypoint(
    name: str, *, kind: Literal["agent", "flow", "eval"], tags: list[str] | None = None
) -> Iterator[None]:
    """Trace-level attributes for one run started from the CLI, the API or an eval case."""
    if not langfuse_enabled():
        yield
        return
    from langfuse import propagate_attributes

    s = get_settings()
    with propagate_attributes(
        trace_name=name,
        user_id=s.dev_name or None,
        tags=[kind, name, *(tags or [])],
        metadata={"entrypoint": kind},
    ):
        yield


@contextmanager
def flow_span(name: str, inputs: Any) -> Iterator[Any]:
    """A flow as one `chain` node with its input/output; the agents it runs nest under it."""
    if not langfuse_enabled():
        with nullcontext() as span:
            yield span
        return
    from langfuse import get_client

    with get_client().start_as_current_observation(
        as_type="chain", name=name, input=_jsonable(inputs)
    ) as span:
        yield span


def set_output(span: Any, output: Any) -> None:
    if span is not None:
        span.update(output=_jsonable(output))
