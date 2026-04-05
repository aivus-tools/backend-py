import json
import logging
import os
import re as _re
import time
from dataclasses import dataclass
from typing import Any

import anthropic
import openai

logger = logging.getLogger(__name__)

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.0},
}

FALLBACK_CHAIN: dict[str, str] = {
    "claude-sonnet-4-5-20250929": "gpt-4o",
    "gpt-4o": "gpt-4o-mini",
}

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_used: str
    latency_ms: int


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
    return (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / 1_000_000


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude")


def _get_openai_client() -> openai.OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        msg = "OPENAI_API_KEY is not set"
        raise ValueError(msg)
    return openai.OpenAI(api_key=api_key)


def _get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        msg = "ANTHROPIC_API_KEY is not set"
        raise ValueError(msg)
    return anthropic.Anthropic(api_key=api_key)


def _call_openai(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,  # noqa: FBT001
) -> LLMResponse:
    client = _get_openai_client()
    start = time.monotonic()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": 120,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    latency_ms = int((time.monotonic() - start) * 1000)

    content = response.choices[0].message.content or ""
    input_tokens = response.usage.prompt_tokens if response.usage else 0
    output_tokens = response.usage.completion_tokens if response.usage else 0

    return LLMResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=calculate_cost(model, input_tokens, output_tokens),
        model_used=model,
        latency_ms=latency_ms,
    )


def _call_anthropic(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,  # noqa: FBT001
) -> LLMResponse:
    client = _get_anthropic_client()
    start = time.monotonic()

    system_content = ""
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_content += msg["content"] + "\n"
        else:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

    if json_mode:
        system_content += "\nRespond with valid JSON only. No markdown, no code blocks."

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if system_content.strip():
        kwargs["system"] = system_content.strip()

    response = client.messages.create(**kwargs)
    latency_ms = int((time.monotonic() - start) * 1000)

    content = response.content[0].text if response.content else ""
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    return LLMResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=calculate_cost(model, input_tokens, output_tokens),
        model_used=model,
        latency_ms=latency_ms,
    )


def call_llm(
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    json_mode: bool = False,  # noqa: FBT001, FBT002
) -> LLMResponse:
    current_model = model
    last_exception = None

    while current_model:
        for attempt in range(MAX_RETRIES):
            try:
                if _is_anthropic_model(current_model):
                    result = _call_anthropic(
                        current_model, messages, temperature, max_tokens, json_mode
                    )
                else:
                    result = _call_openai(
                        current_model, messages, temperature, max_tokens, json_mode
                    )

                logger.info(
                    "LLM call: model=%s tokens=%d/%d cost=$%.6f latency=%dms",
                    result.model_used,
                    result.input_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    result.latency_ms,
                )
                return result

            except Exception as exc:
                last_exception = exc
                logger.warning(
                    "LLM call failed: model=%s attempt=%d/%d error=%s",
                    current_model,
                    attempt + 1,
                    MAX_RETRIES,
                    str(exc),
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY * (2**attempt))

        fallback = FALLBACK_CHAIN.get(current_model)
        if fallback:
            logger.warning(
                "Falling back: %s -> %s",
                current_model,
                fallback,
            )
            current_model = fallback
        else:
            break

    msg = f"All LLM attempts exhausted for {model}"
    raise RuntimeError(msg) from last_exception


_JSON_BLOCK_RE = _re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", _re.DOTALL)


def call_llm_json(
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> tuple[dict, LLMResponse]:
    response = call_llm(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )

    content = response.content.strip()

    try:
        parsed = json.loads(content)
        return parsed, response
    except json.JSONDecodeError:
        pass

    match = _JSON_BLOCK_RE.search(content)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            return parsed, response
        except json.JSONDecodeError:
            pass

    logger.error("LLM returned invalid JSON: model=%s content=%s", model, content[:500])
    msg = f"LLM returned invalid JSON from {model}"
    raise ValueError(msg)
