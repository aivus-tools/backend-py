import json
import logging
import os
import re as _re
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import cast

import anthropic
import openai
from google import genai
from google.genai import types as genai_types
from google.oauth2 import service_account

logger = logging.getLogger(__name__)


def _part_summary(part: dict) -> dict:
    """Trace-friendly summary of a multimodal content part."""
    kind = part.get("type", "text")
    if kind == "text":
        return {"type": "text", "text": part.get("text", "")}
    if kind == "file_uri":
        return {
            "type": "file_uri",
            "file_uri": part.get("file_uri", ""),
            "mime_type": part.get("mime_type", ""),
        }
    if kind == "inline_bytes":
        data = part.get("data")
        size = len(data) if isinstance(data, (bytes, bytearray)) else 0
        return {
            "type": "inline_bytes",
            "size": size,
            "mime_type": part.get("mime_type", ""),
        }
    return {"type": kind}


def _serialize_message_for_trace(message: dict) -> dict:
    content = message.get("content")
    if isinstance(content, str):
        return {"role": message["role"], "content": content}
    parts = [_part_summary(x) for x in (content or [])]
    return {"role": message["role"], "parts": parts}


def _flatten_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(part.get("text", ""))
        elif part.get("type") == "file_uri":
            texts.append(f"[attachment: {part.get('file_uri', '')}]")
        elif part.get("type") == "inline_bytes":
            texts.append(f"[attachment: {part.get('mime_type', 'binary')}]")
    return "\n".join(t for t in texts if t)


MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.0},
    "gemini-3.1-flash-lite-preview": {"input": 0.10, "output": 0.40},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}

FALLBACK_CHAIN: dict[str, str] = {
    "claude-sonnet-4-5-20250929": "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview": "gemini-2.5-pro",
    "gemini-3.1-flash-lite-preview": "gemini-2.5-flash-lite",
    "gemini-2.5-pro": "gemini-2.5-flash",
    "gemini-2.5-flash": "gemini-2.5-flash-lite",
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
    request_messages: list[dict[str, str]] = field(default_factory=list)
    request_params: dict[str, Any] = field(default_factory=dict)


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
    return (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / 1_000_000


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude")


def _is_gemini_model(model: str) -> bool:
    return model.startswith("gemini")


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


def _get_gemini_client() -> genai.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project:
        msg = "GOOGLE_CLOUD_PROJECT is not set"
        raise ValueError(msg)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    client_kwargs: dict[str, Any] = {
        "vertexai": True,
        "project": project,
        "location": location,
    }

    credentials_path = os.environ.get("VERTEX_CREDENTIALS_PATH", "")
    if credentials_path:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        client_kwargs["credentials"] = credentials

    return genai.Client(**client_kwargs)


def _gemini_parts(content: Any) -> list[genai_types.Part]:  # noqa: C901
    """Convert our content representation to list of genai Parts."""
    if isinstance(content, str):
        return [genai_types.Part.from_text(text=content)]
    if not isinstance(content, list):
        return [genai_types.Part.from_text(text=str(content or ""))]

    parts: list[genai_types.Part] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "text")
        if kind == "text":
            text = item.get("text", "")
            if text:
                parts.append(genai_types.Part.from_text(text=text))
        elif kind == "file_uri":
            uri = item.get("file_uri") or ""
            mime = item.get("mime_type") or ""
            if uri and mime:
                parts.append(genai_types.Part.from_uri(file_uri=uri, mime_type=mime))
        elif kind == "inline_bytes":
            data = item.get("data")
            mime = item.get("mime_type") or ""
            if isinstance(data, (bytes, bytearray)) and mime:
                parts.append(
                    genai_types.Part.from_bytes(data=bytes(data), mime_type=mime)
                )
    if not parts:
        parts.append(genai_types.Part.from_text(text=""))
    return parts


def _call_gemini(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,  # noqa: FBT001
) -> LLMResponse:
    client = _get_gemini_client()
    start = time.monotonic()

    system_parts: list[str] = []
    contents: list[genai_types.Content] = []
    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        if role == "system":
            system_parts.append(_flatten_content_to_text(content))
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append(
            genai_types.Content(role=gemini_role, parts=_gemini_parts(content))
        )

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system_parts:
        config_kwargs["system_instruction"] = "\n".join(p for p in system_parts if p)
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    # Gemini 3.1 Pro enables dynamic "thinking" by default which eats into
    # max_output_tokens and can truncate JSON replies mid-stream. Cap the
    # thinking budget so the visible answer always has room to complete.
    if model.startswith("gemini-3"):
        try:
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=0,
            )
        except Exception:
            logger.debug("ThinkingConfig not supported for %s", model)

    response = client.models.generate_content(
        model=model,
        contents=cast("Any", contents),
        config=genai_types.GenerateContentConfig(**config_kwargs),
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    content = response.text or ""
    usage = response.usage_metadata
    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage, "candidates_token_count", 0) or 0

    return LLMResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=calculate_cost(model, input_tokens, output_tokens),
        model_used=model,
        latency_ms=latency_ms,
        request_messages=[_serialize_message_for_trace(x) for x in messages],
        request_params={
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
            "engine": "gemini",
        },
    )


def _call_openai(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,  # noqa: FBT001
) -> LLMResponse:
    client = _get_openai_client()
    start = time.monotonic()

    flat_messages = [
        {"role": m["role"], "content": _flatten_content_to_text(m.get("content", ""))}
        for m in messages
    ]

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": flat_messages,
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
        request_messages=[_serialize_message_for_trace(x) for x in messages],
        request_params={
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
            "engine": "openai",
        },
    )


def _call_anthropic(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,  # noqa: FBT001
) -> LLMResponse:
    client = _get_anthropic_client()
    start = time.monotonic()

    system_content = ""
    api_messages = []
    for msg in messages:
        flat = _flatten_content_to_text(msg.get("content", ""))
        if msg["role"] == "system":
            system_content += flat + "\n"
        else:
            api_messages.append({"role": msg["role"], "content": flat})

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
        request_messages=[_serialize_message_for_trace(x) for x in messages],
        request_params={
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
            "engine": "anthropic",
        },
    )


def call_llm(
    model: str,
    messages: list[dict[str, Any]],
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
                elif _is_gemini_model(current_model):
                    result = _call_gemini(
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
    messages: list[dict[str, Any]],
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
