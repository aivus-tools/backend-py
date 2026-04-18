"""Legacy AI helpers kept for the vendor-offer flows.

Only `analyze_brief` and `analyze_comparison` remain — everything related to
the old brief chat flow has moved to `ai_brief_v3.py`.
"""

import json
import logging

from aivus_backend.core.llm import call_llm_json

logger = logging.getLogger(__name__)

MODEL_ANALYSIS = "gemini-2.5-flash"
MODEL_COMPARISON = "gemini-3.1-pro-preview"


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


def analyze_brief(brief_data: dict) -> dict:
    """Analyze a brief and provide suggestions."""
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
    """Analyze and compare vendor offers using AI."""
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


def process_chat_message(
    user_message: str,
    history: list,
    extracted_fields: dict | None = None,
) -> dict:
    """Legacy V1 chat — kept as a stub so old views keep importing.

    The v1 brief chat flow is gone; the `client/briefs/chat*` endpoints are
    considered deprecated. Returns a structured-error response so old callers
    don't crash silently if anything hits them.
    """
    return {
        "reply": ("AI brief chat has moved. Please use the new flow at /public-brief."),
        "brief_data": None,
        "is_complete": False,
        "extracted_fields": {},
    }
