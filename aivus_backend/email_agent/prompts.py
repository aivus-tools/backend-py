"""Prompt loading and trace helpers for the email agent (Stage 3).

Thin copies of the brief-chat helpers so the email agent does not import the
brief pipeline. Prompts live in the DB (``BriefPrompt``) and stay admin-editable;
the model is a per-prompt override with a fallback default. The trace kept on the
``AgentLog`` is deliberately compact — model and cost, not the raw email — since
the log is human-readable and the body is untrusted client data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from aivus_backend.projects.models import BriefPrompt

if TYPE_CHECKING:
    from aivus_backend.core.llm import LLMResponse
    from aivus_backend.email_agent.models import VendorAgentProfile

DEFAULT_MODEL = "gemini-3.1-pro-preview"

_INSTRUCTIONS_PLACEHOLDER = "{vendor_instructions}"

DEFAULT_AGENT_INSTRUCTION = (
    "You represent a video production vendor and speak on their behalf to their "
    "clients. You have not been briefed on this vendor's services, tone, or rules "
    "yet, so stay cautious: be warm and professional, acknowledge the client, and "
    "never state prices, timelines, estimates, availability, or any commitment. "
    "When anything is unclear or would require a commitment, hand off to the "
    "producer rather than guessing."
)


def load_prompt_body(slug: str) -> str:
    return BriefPrompt.get_active_body(slug=slug, default="")


def model_for_prompt(slug: str) -> str:
    prompt = BriefPrompt.get_active(slug=slug)
    if prompt and prompt.model_name:
        return prompt.model_name
    return DEFAULT_MODEL


def compile_vendor_instructions(profile: VendorAgentProfile | None) -> str:
    """Compile the vendor's own instruction, falling back to a cautious default.

    A vendor who has not written an instruction yet must not leave the agent with
    an empty persona — an unconstrained model is the dangerous case — so the
    cautious default stands in until they onboard.
    """
    if profile is None:
        return DEFAULT_AGENT_INSTRUCTION
    parts = [profile.system_prompt, profile.tone, profile.business_context]
    parts.extend(f"- {rule}" for rule in profile.special_rules or [])
    compiled = "\n".join(part for part in parts if part).strip()
    return compiled or DEFAULT_AGENT_INSTRUCTION


def fill_instructions(body: str, instructions: str) -> str:
    """Insert vendor instructions without ``str.format``.

    The prompt bodies contain literal ``{...}`` describing the JSON schema, so
    ``format`` would raise; only the explicit placeholder is replaced.
    """
    return body.replace(_INSTRUCTIONS_PLACEHOLDER, instructions)


def trace_entry(purpose: str, response: LLMResponse) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "model": response.model_used,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "latency_ms": response.latency_ms,
    }
