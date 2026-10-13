"""Asks the LLM for mapping proposals. Proposal generation only: no approval, no execution."""

from dataclasses import dataclass

from opentelemetry import trace

from app.agent.llm_client import LLMClient, ToolCallResult
from app.agent.prompts import SYSTEM_PROMPT, build_user_message, detect_instructions
from app.agent.tool_registry import PROPOSE_FIELD_MAPPINGS
from app.schemas.mapping import ProposeFieldMappingsArgs
from app.schemas.normalized import NormalizedSchema

tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class AgentProposal:
    args: ProposeFieldMappingsArgs
    call: ToolCallResult
    untrusted_fields: frozenset[str]


def find_untrusted_fields(source: NormalizedSchema) -> frozenset[str]:
    return frozenset(f.key for f in source.fields if detect_instructions(f))


def propose_mappings(
    llm: LLMClient, source: NormalizedSchema, target: NormalizedSchema
) -> AgentProposal:
    with tracer.start_as_current_span("llm.propose_field_mappings") as span:
        call = llm.call_tool(
            SYSTEM_PROMPT, build_user_message(source, target), PROPOSE_FIELD_MAPPINGS
        )
        # Metadata only: prompts and outputs are never put on spans.
        span.set_attribute("llm.provider", call.provider)
        span.set_attribute("llm.model", call.model)
        span.set_attribute("llm.input_tokens", call.input_tokens or 0)
        span.set_attribute("llm.output_tokens", call.output_tokens or 0)
    assert isinstance(call.arguments, ProposeFieldMappingsArgs)  # noqa: S101 - tool contract
    return AgentProposal(
        args=call.arguments, call=call, untrusted_fields=find_untrusted_fields(source)
    )
