"""AI-powered brief creation using LangGraph.

This module implements a conversational AI agent that guides clients through
creating a video production brief. It uses LangGraph for state management
and routes all LLM calls through ``aivus_backend.core.llm``.
"""

import json
import logging
import threading
from typing import Annotated

from langgraph.graph import END
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from aivus_backend.core.llm import call_llm_json

MODEL_CHAT = "gemini-2.5-flash"
MODEL_ANALYSIS = "gemini-2.5-flash"
MODEL_COMPARISON = "gemini-2.5-pro"

logger = logging.getLogger(__name__)

# Brief fields schema - what we need to extract from conversation
BRIEF_FIELDS = {
    "projectName": {"description": "Name of the video project", "required": True},
    "description": {"description": "Short project description", "required": False},
    "clientName": {"description": "Client company name", "required": False},
    "brandName": {"description": "Brand name for the video", "required": False},
    "projectDescription": {
        "description": (
            "Detailed description of the video project"
            " - what kind of video, its purpose, target audience"
        ),
        "required": True,
    },
    "referenceVideos": {
        "description": "List of reference video URLs with comments",
        "required": False,
    },
    "distributionAndAdPlacements": {
        "description": (
            "Where the video will be distributed (TV, YouTube, social media, etc.)"
        ),
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

SYSTEM_PROMPT = (
    "You are AIVUS AI, a friendly and professional"
    " video production brief creation assistant.\n"
    "Your job is to help clients create comprehensive"
    " briefs for video production projects.\n"
    "\n"
    "You should be conversational, warm, and guide the"
    " user naturally. Ask 2-3 questions at a time"
    " maximum.\n"
    "Don't overwhelm the user with too many questions"
    " at once.\n"
    "\n"
    "IMPORTANT RULES:\n"
    "1. Start by understanding what kind of video the"
    " client wants (commercial, music video,"
    " corporate, etc.)\n"
    "2. Ask about the project name and a brief"
    " description of what they envision\n"
    "3. Then ask about technical specs: duration, any"
    " additional cuts/versions needed\n"
    "4. Ask about distribution (where will it air/be"
    " posted)\n"
    "5. Ask about budget if they haven't mentioned it\n"
    "6. Ask about shooting requirements, territory,"
    " and timeline\n"
    "7. Reference videos are nice-to-have, ask about"
    " them naturally\n"
    "\n"
    "When you have enough information to create a brief"
    " (at minimum: project name, description, and video"
    " duration),\n"
    "you can produce the brief. You don't need ALL"
    " fields - just the essential ones.\n"
    "\n"
    "CONVERSATION STYLE:\n"
    "- Be enthusiastic about their project\n"
    "- Use natural language, not robotic forms\n"
    "- Acknowledge what they've told you before asking"
    " more\n"
    "- Keep responses concise (2-4 sentences +"
    " questions)\n"
    "- If they give you a lot of info at once, extract"
    " everything you can\n"
    "\n"
    "You will be given the current conversation history"
    " and the fields extracted so far.\n"
    "Your response must be a JSON object with two"
    " fields:\n"
    '- "reply": Your conversational response to the'
    " user\n"
    '- "extracted_fields": A JSON object with any NEW'
    " fields you extracted from the LATEST user"
    " message\n"
    '- "is_complete": boolean - true if you have enough'
    " info to produce a complete brief\n"
    "\n"
    "For extracted_fields, use this schema:\n"
    "{\n"
    '    "projectName": "string",\n'
    '    "description": "string",\n'
    '    "clientName": "string",\n'
    '    "brandName": "string",\n'
    '    "projectDescription": "detailed string",\n'
    '    "referenceVideos": [{"url": "string",'
    ' "comment": "string"}],\n'
    '    "distributionAndAdPlacements": "string",\n'
    '    "territory": ["string"],\n'
    '    "term": {"length": "string",'
    ' "unit": "string"},\n'
    '    "mainVideoDuration": {"number": "1",'
    ' "length": "30", "timeUnit": "sec",'
    ' "comment": ""},\n'
    '    "cuts": [{"number": "1", "length": "15",'
    ' "timeUnit": "sec", "comment": ""}],\n'
    '    "shootingDays": {"number": "string",'
    ' "length": "string", "comment": "",'
    ' "timeUnit": ""},\n'
    '    "budget": number,\n'
    '    "visibleForVendors": true\n'
    "}\n"
    "\n"
    "Only include fields that were NEWLY mentioned in"
    " the latest message. Do not repeat previously"
    " extracted fields.\n"
    "\n"
    "IMPORTANT: Always respond with valid JSON only."
    " No markdown, no code blocks, just JSON."
)

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

COMPARISON_SYSTEM_PROMPT = (
    "You are AIVUS AI, an expert video production"
    " cost analyst.\n"
    "You help clients understand and compare vendor"
    " offers for video production projects.\n"
    "\n"
    "Given the brief details and comparison data from"
    " multiple vendors, provide insightful analysis.\n"
    "Focus on:\n"
    "1. Price differences and what they might mean\n"
    "2. Which vendor appears to offer better value for"
    " specific categories\n"
    "3. Any red flags (unusually low or high prices)\n"
    "4. Overall recommendations\n"
    "\n"
    "Be balanced, professional, and data-driven. Use"
    " specific numbers from the data.\n"
    "If the user asks a specific question, focus your"
    " analysis on that question.\n"
    "\n"
    "Respond with a JSON object:\n"
    "{\n"
    '    "analysis": "Your detailed analysis text'
    ' (can use markdown formatting)",\n'
    '    "highlights": [\n'
    '        {"type": "positive|negative|neutral",'
    ' "text": "A specific highlight or finding"}\n'
    "    ]\n"
    "}"
)


def _merge_extracted_fields(existing: dict, new_fields: dict) -> dict:
    """Merge newly extracted fields into existing brief data."""
    merged = dict(existing)
    merged.update(
        {
            key: value
            for key, value in new_fields.items()
            if value is not None and value not in ("", [], {})
        }
    )
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
    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if state.get("extracted_fields"):
        context_msg = (
            f"Fields extracted so far from the conversation: "
            f"{json.dumps(state['extracted_fields'], indent=2)}\n"
            f"Continue the conversation to fill in missing required fields."
        )
        llm_messages.append({"role": "system", "content": context_msg})

    llm_messages.extend(
        {"role": msg["role"], "content": msg["content"]} for msg in state["messages"]
    )

    try:
        parsed, _ = call_llm_json(
            model=MODEL_CHAT,
            messages=llm_messages,
            temperature=0.7,
            max_tokens=1000,
        )

        fallback_reply = (
            "I'd love to help you create a brief!"
            " What kind of video project are you"
            " working on?"
        )
        reply = parsed.get("reply", fallback_reply)
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

    except (json.JSONDecodeError, ValueError):
        logger.exception("Failed to parse LLM response as JSON")
        parse_error_reply = (
            "I'd love to help you create a brief!"
            " Could you tell me about your video"
            " project?"
        )
        return {
            "reply": parse_error_reply,
            "is_complete": False,
            "messages": [
                {
                    "role": "assistant",
                    "content": parse_error_reply,
                }
            ],
        }
    except Exception:
        logger.exception("Error in chat node")
        error_reply = "I encountered a temporary issue. Could you please try again?"
        return {
            "reply": error_reply,
            "is_complete": False,
            "messages": [
                {
                    "role": "assistant",
                    "content": error_reply,
                }
            ],
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
        "mainVideoDuration": fields.get(
            "mainVideoDuration",
            {"number": "", "length": "", "timeUnit": "sec", "comment": ""},
        ),
        "cuts": fields.get("cuts", []),
        "shootingDays": fields.get(
            "shootingDays", {"number": "", "length": "", "comment": "", "timeUnit": ""}
        ),
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
    builder.add_conditional_edges(
        "chat", should_end, {"finalize": "finalize", END: END}
    )
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


def process_chat_message(
    user_message: str, history: list, extracted_fields: dict | None = None
) -> dict:
    """Process a user message in the brief creation chat.

    Args:
        user_message: The user's latest message
        history: List of previous messages
            [{"role": "user"|"assistant", "content": "..."}]
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
    safe_history = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in (history or [])
        if isinstance(msg, dict)
        and msg.get("role") in ("user", "assistant")
        and isinstance(msg.get("content"), str)
    ][-20:]

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
        "brief_data": result.get("extracted_fields")
        if result.get("is_complete")
        else None,
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
    try:
        parsed, _ = call_llm_json(
            model=MODEL_ANALYSIS,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyze this video production"
                        " brief:\n" + json.dumps(brief_data, indent=2)
                    ),
                },
            ],
            temperature=0.5,
            max_tokens=1000,
        )

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


def analyze_comparison(
    brief_data: dict, comparison_data: dict, question: str | None = None
) -> dict:
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
        parsed, _ = call_llm_json(
            model=MODEL_COMPARISON,
            messages=messages,
            temperature=0.5,
            max_tokens=2000,
        )

        return {
            "analysis": parsed.get("analysis", "Analysis complete."),
            "highlights": parsed.get("highlights", []),
        }

    except Exception:
        logger.exception("Error analyzing comparison")
        return {
            "analysis": (
                "Unable to analyze the comparison at this time. Please try again."
            ),
            "highlights": [],
        }
