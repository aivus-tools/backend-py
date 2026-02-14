"""AI-powered brief creation using LangGraph + OpenAI.

This module implements a conversational AI agent that guides clients through
creating a video production brief. It uses LangGraph for state management
and OpenAI for natural language understanding and generation.
"""

import json
import logging
import os
import threading
from typing import Annotated

from langgraph.graph import END
from langgraph.graph import StateGraph
from openai import OpenAI
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

# Brief fields schema - what we need to extract from conversation
BRIEF_FIELDS = {
    "projectName": {"description": "Name of the video project", "required": True},
    "description": {"description": "Short project description", "required": False},
    "clientName": {"description": "Client company name", "required": False},
    "brandName": {"description": "Brand name for the video", "required": False},
    "projectDescription": {
        "description": "Detailed description of the video project - what kind of video, its purpose, target audience",
        "required": True,
    },
    "referenceVideos": {
        "description": "List of reference video URLs with comments",
        "required": False,
    },
    "distributionAndAdPlacements": {
        "description": "Where the video will be distributed (TV, YouTube, social media, etc.)",
        "required": False,
    },
    "territory": {
        "description": "Geographic territories for the video distribution",
        "required": False,
    },
    "term": {
        "description": "License term - length and unit (months/years)",
        "required": False,
    },
    "mainVideoDuration": {
        "description": "Main video duration - number, length in seconds/minutes",
        "required": True,
    },
    "cuts": {
        "description": "Additional cuts/versions - number, lengths",
        "required": False,
    },
    "shootingDays": {
        "description": "Number of shooting days and their length",
        "required": False,
    },
    "budget": {
        "description": "Total budget for the project in USD",
        "required": False,
    },
}

SYSTEM_PROMPT = """You are AIVUS AI, a friendly and professional video production brief creation assistant.
Your job is to help clients create comprehensive briefs for video production projects.

You should be conversational, warm, and guide the user naturally. Ask 2-3 questions at a time maximum.
Don't overwhelm the user with too many questions at once.

IMPORTANT RULES:
1. Start by understanding what kind of video the client wants (commercial, music video, corporate, etc.)
2. Ask about the project name and a brief description of what they envision
3. Then ask about technical specs: duration, any additional cuts/versions needed
4. Ask about distribution (where will it air/be posted)
5. Ask about budget if they haven't mentioned it
6. Ask about shooting requirements, territory, and timeline
7. Reference videos are nice-to-have, ask about them naturally

When you have enough information to create a brief (at minimum: project name, description, and video duration),
you can produce the brief. You don't need ALL fields - just the essential ones.

CONVERSATION STYLE:
- Be enthusiastic about their project
- Use natural language, not robotic forms
- Acknowledge what they've told you before asking more
- Keep responses concise (2-4 sentences + questions)
- If they give you a lot of info at once, extract everything you can

You will be given the current conversation history and the fields extracted so far.
Your response must be a JSON object with two fields:
- "reply": Your conversational response to the user
- "extracted_fields": A JSON object with any NEW fields you extracted from the LATEST user message
- "is_complete": boolean - true if you have enough info to produce a complete brief

For extracted_fields, use this schema:
{
    "projectName": "string",
    "description": "string",
    "clientName": "string",
    "brandName": "string",
    "projectDescription": "detailed string",
    "referenceVideos": [{"url": "string", "comment": "string"}],
    "distributionAndAdPlacements": "string",
    "territory": ["string"],
    "term": {"length": "string", "unit": "string"},
    "mainVideoDuration": {"number": "1", "length": "30", "timeUnit": "sec", "comment": ""},
    "cuts": [{"number": "1", "length": "15", "timeUnit": "sec", "comment": ""}],
    "shootingDays": {"number": "string", "length": "string", "comment": "", "timeUnit": ""},
    "budget": number,
    "visibleForVendors": true
}

Only include fields that were NEWLY mentioned in the latest message. Do not repeat previously extracted fields.

IMPORTANT: Always respond with valid JSON only. No markdown, no code blocks, just JSON."""

ANALYSIS_SYSTEM_PROMPT = """You are AIVUS AI, an expert video production analyst.
Analyze the given brief data and provide:
1. A summary of the brief
2. Suggestions for improvement or missing information
3. Potential concerns or things to clarify

Be helpful, professional, and specific. Focus on actionable suggestions.
Respond with a JSON object:
{
    "summary": "A 2-3 sentence summary of the brief",
    "suggestions": ["suggestion 1", "suggestion 2", ...]
}"""

COMPARISON_SYSTEM_PROMPT = """You are AIVUS AI, an expert video production cost analyst.
You help clients understand and compare vendor offers for video production projects.

Given the brief details and comparison data from multiple vendors, provide insightful analysis.
Focus on:
1. Price differences and what they might mean
2. Which vendor appears to offer better value for specific categories
3. Any red flags (unusually low or high prices)
4. Overall recommendations

Be balanced, professional, and data-driven. Use specific numbers from the data.
If the user asks a specific question, focus your analysis on that question.

Respond with a JSON object:
{
    "analysis": "Your detailed analysis text (can use markdown formatting)",
    "highlights": [
        {"type": "positive|negative|neutral", "text": "A specific highlight or finding"}
    ]
}"""


def _get_openai_client():
    """Get OpenAI client with API key from environment."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        msg = "OPENAI_API_KEY environment variable is not set"
        raise ValueError(msg)
    return OpenAI(api_key=api_key)


def _merge_extracted_fields(existing: dict, new_fields: dict) -> dict:
    """Merge newly extracted fields into existing brief data."""
    merged = dict(existing)
    for key, value in new_fields.items():
        if value is not None and value != "" and value != [] and value != {}:
            merged[key] = value
    return merged


# ==================== LangGraph State & Nodes ====================


def _add_messages(left: list, right: list) -> list:
    """Reducer that appends messages."""
    return left + right


class BriefChatState(TypedDict):
    """State for the brief creation chat graph."""

    messages: Annotated[list, _add_messages]
    extracted_fields: dict
    reply: str
    is_complete: bool


def chat_node(state: BriefChatState) -> dict:
    """Main chat node - processes user message and generates response."""
    client = _get_openai_client()

    # Build the conversation for OpenAI
    openai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add context about what we've extracted so far
    if state.get("extracted_fields"):
        context_msg = (
            f"Fields extracted so far from the conversation: "
            f"{json.dumps(state['extracted_fields'], indent=2)}\n"
            f"Continue the conversation to fill in missing required fields."
        )
        openai_messages.append({"role": "system", "content": context_msg})

    # Add conversation history
    for msg in state["messages"]:
        openai_messages.append({"role": msg["role"], "content": msg["content"]})

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=openai_messages,
            temperature=0.7,
            max_tokens=1000,
            response_format={"type": "json_object"},
            timeout=60,
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)

        reply = parsed.get("reply", "I'd love to help you create a brief! What kind of video project are you working on?")
        new_fields = parsed.get("extracted_fields", {})
        is_complete = parsed.get("is_complete", False)

        # Merge new fields with existing
        merged_fields = _merge_extracted_fields(
            state.get("extracted_fields", {}),
            new_fields,
        )

        return {
            "extracted_fields": merged_fields,
            "reply": reply,
            "is_complete": is_complete,
            "messages": [{"role": "assistant", "content": reply}],
        }

    except json.JSONDecodeError:
        logger.exception("Failed to parse OpenAI response as JSON")
        return {
            "reply": "I'd love to help you create a brief! Could you tell me about your video project?",
            "is_complete": False,
            "messages": [{"role": "assistant", "content": "I'd love to help you create a brief! Could you tell me about your video project?"}],
        }
    except Exception:
        logger.exception("Error in chat node")
        return {
            "reply": "I encountered a temporary issue. Could you please try again?",
            "is_complete": False,
            "messages": [{"role": "assistant", "content": "I encountered a temporary issue. Could you please try again?"}],
        }


def should_end(state: BriefChatState) -> str:
    """Determine if the conversation should end."""
    if state.get("is_complete"):
        return "finalize"
    return END


def finalize_node(state: BriefChatState) -> dict:
    """Finalize the brief data when the conversation is complete."""
    fields = state.get("extracted_fields", {})

    # Build complete brief details structure
    brief_data = {
        "crmId": fields.get("crmId", ""),
        "clientName": fields.get("clientName", ""),
        "projectName": fields.get("projectName", ""),
        "description": fields.get("description", ""),
        "irsEin": fields.get("irsEin", ""),
        "brandName": fields.get("brandName", ""),
        "managers": fields.get("managers", []),
        "projectDescription": fields.get("projectDescription", ""),
        "referenceVideos": fields.get("referenceVideos", []),
        "distributionAndAdPlacements": fields.get("distributionAndAdPlacements", ""),
        "territory": fields.get("territory", []),
        "collaborators": fields.get("collaborators", []),
        "term": fields.get("term", {"length": "", "unit": ""}),
        "mainVideoDuration": fields.get("mainVideoDuration", {"number": "", "length": "", "timeUnit": "sec", "comment": ""}),
        "cuts": fields.get("cuts", []),
        "shootingDays": fields.get("shootingDays", {"number": "", "length": "", "comment": "", "timeUnit": ""}),
        "estimationTemplate": fields.get("estimationTemplate", ""),
        "budget": fields.get("budget", 0),
        "visibleForVendors": fields.get("visibleForVendors", True),
    }

    return {
        "extracted_fields": brief_data,
    }


# Build the LangGraph
def _build_chat_graph():
    """Build and compile the brief creation chat graph."""
    builder = StateGraph(BriefChatState)

    builder.add_node("chat", chat_node)
    builder.add_node("finalize", finalize_node)

    builder.set_entry_point("chat")
    builder.add_conditional_edges("chat", should_end, {"finalize": "finalize", END: END})
    builder.add_edge("finalize", END)

    return builder.compile()


# Singleton graph instance
_chat_graph = None
_chat_graph_lock = threading.Lock()


def _get_chat_graph():
    """Get or create the chat graph singleton."""
    global _chat_graph  # noqa: PLW0603
    if _chat_graph is None:
        with _chat_graph_lock:
            if _chat_graph is None:
                _chat_graph = _build_chat_graph()
    return _chat_graph


# ==================== Public API ====================


def process_chat_message(user_message: str, history: list, extracted_fields: dict | None = None) -> dict:
    """Process a user message in the brief creation chat.

    Args:
        user_message: The user's latest message
        history: List of previous messages [{"role": "user"|"assistant", "content": "..."}]
        extracted_fields: Previously extracted brief fields

    Returns:
        dict with keys:
        - reply: The AI's response
        - brief_data: Complete brief data if is_complete=True, else None
        - is_complete: Whether the brief is ready
        - extracted_fields: Current state of extracted fields
    """
    graph = _get_chat_graph()

    # QA4-025: Sanitize history — only allow valid roles and limit to last 20 messages
    safe_history = []
    if history:
        for msg in history:
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant") and isinstance(msg.get("content"), str):
                safe_history.append({"role": msg["role"], "content": msg["content"]})
        safe_history = safe_history[-20:]

    # Build messages list from sanitized history + new message
    messages = safe_history
    messages.append({"role": "user", "content": user_message})

    initial_state = {
        "messages": messages,
        "extracted_fields": extracted_fields or {},
        "reply": "",
        "is_complete": False,
    }

    result = graph.invoke(initial_state)

    return {
        "reply": result.get("reply", ""),
        "brief_data": result.get("extracted_fields") if result.get("is_complete") else None,
        "is_complete": result.get("is_complete", False),
        "extracted_fields": result.get("extracted_fields", {}),
    }


def analyze_brief(brief_data: dict) -> dict:
    """Analyze a brief and provide suggestions.

    Args:
        brief_data: The brief details to analyze

    Returns:
        dict with keys:
        - summary: Brief summary
        - suggestions: List of suggestions
    """
    client = _get_openai_client()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze this video production brief:\n{json.dumps(brief_data, indent=2)}"},
            ],
            temperature=0.5,
            max_tokens=1000,
            response_format={"type": "json_object"},
            timeout=60,
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)

        return {
            "summary": parsed.get("summary", "Brief analysis complete."),
            "suggestions": parsed.get("suggestions", []),
        }

    except Exception:
        logger.exception("Error analyzing brief")
        return {
            "summary": "Unable to analyze the brief at this time.",
            "suggestions": ["Please try again later."],
        }


def analyze_comparison(brief_data: dict, comparison_data: dict, question: str | None = None) -> dict:
    """Analyze and compare vendor offers using AI.

    Args:
        brief_data: The brief details
        comparison_data: The comparison data with vendors and categories
        question: Optional follow-up question from the user

    Returns:
        dict with keys:
        - analysis: Detailed analysis text
        - highlights: List of highlights with type and text
    """
    client = _get_openai_client()

    data_content = (
        f"Brief details:\n{json.dumps(brief_data, indent=2)}\n\n"
        f"Comparison data:\n{json.dumps(comparison_data, indent=2)}"
    )

    messages = [
        {"role": "system", "content": COMPARISON_SYSTEM_PROMPT},
        {"role": "user", "content": data_content},
    ]

    if question:
        messages.append({"role": "user", "content": question})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.5,
            max_tokens=2000,
            response_format={"type": "json_object"},
            timeout=60,
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)

        return {
            "analysis": parsed.get("analysis", "Analysis complete."),
            "highlights": parsed.get("highlights", []),
        }

    except Exception:
        logger.exception("Error analyzing comparison")
        return {
            "analysis": "Unable to analyze the comparison at this time. Please try again.",
            "highlights": [],
        }
