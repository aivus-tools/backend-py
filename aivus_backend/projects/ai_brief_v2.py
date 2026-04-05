import json
import logging
from typing import Annotated

from langgraph.graph import END
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from aivus_backend.core.llm import LLMResponse
from aivus_backend.core.llm import call_llm_json
from aivus_backend.core.sanitize import sanitize_sections
from aivus_backend.projects.models import BRIEF_SECTION_KEYS

logger = logging.getLogger(__name__)

MODEL_ROUTER = "gpt-4o-mini"
MODEL_GENERATION = "gpt-4o"
MODEL_CHAT = "gpt-4o-mini"

SECTION_LABELS = {
    "project_header": "1. Project Header",
    "budget_timeline": "2. Budget & Timeline",
    "strategic_foundation": "3. Strategic Foundation (Creative Brief)",
    "creative_direction": "4. Creative Direction & Visuals",
    "scope_video": "5. Scope of Work: Video Production",
    "scope_photo": "6. Scope of Work: Photography & Design",
    "post_production": "7. Post-Production & Tech",
    "usage_rights": "8. Usage Rights & Licensing",
    "deliverables": "9. Deliverables (Asset List)",
}

MAX_ARCHETYPE_CODE = 6

ARCHETYPE_NAMES = {
    1: "Creative Development & Concepting",
    2: "High-End / Premium Production",
    3: "Content Production / Social Media",
    4: "Post-Production & VFX",
    5: "Photography & Design",
    6: "Key Visual / Design Campaign",
}


def _add_messages(left: list, right: list) -> list:
    return left + right


class BriefGraphState(TypedDict):
    messages: Annotated[list, _add_messages]
    brief_id: str
    document_sections: dict
    structured_data: dict
    archetypes: list[int]
    sections_status: dict
    pending_sections: list[str]
    conversation_phase: str
    questions_asked: list[str]
    current_question_section: str
    methodology_context: str
    feedback_context: str
    reply: str
    sections_changed: list[str]
    section_patches: dict
    turn_input_tokens: int
    turn_output_tokens: int
    turn_cost_usd: float
    model_used: str
    route_intent: str


SECTION_TEMPLATE = """Brief sections (HTML format with data-section attributes):

1. project_header: Project Title, Client/Brand, Agency, Project Type, Archetype(s)
2. budget_timeline: Total Budget Range, Timeline/Key Dates, Payment Terms
3. strategic_foundation: Campaign Objective, Target Audience, Key Message, Tone & Mood, Competitive Context
4. creative_direction: Visual Style, Reference Videos/Moodboards, Color Palette, Typography notes, Music/Sound direction
5. scope_video: Format/Duration, Number of Deliverables, Talent Requirements, Locations, Crew Requirements, Equipment, Shooting Schedule
6. scope_photo: Stills needed, Settings/Scenes, Retouching level
7. post_production: Editing Style, VFX/Animation, Color Grading, Sound Design, Music Licensing
8. usage_rights: Media Types, Territories, Term Duration, Talent Usage, Music Licensing
9. deliverables: Full Asset List with specs (format, resolution, duration, aspect ratio)

Each section should be wrapped in a div: <div data-section="section_key">...</div>
Use semantic HTML: h2 for section titles, ul/li for lists, strong for labels, p for text.
"""

GENERATE_SYSTEM_PROMPT = (
    """You are AIVUS, an expert video production brief creation system.

Your task: generate a COMPLETE professional production brief from the client's description.
This is the client's FIRST message. Create the WOW-effect by generating a full, detailed brief instantly.

{methodology_context}

{feedback_context}

"""
    + SECTION_TEMPLATE
    + """

INSTRUCTIONS:
1. Analyze the client's message and determine project archetype(s):
   1=Creative Development, 2=High-End Production, 3=Content/Social, 4=Post-Production, 5=Photography, 6=Key Visual/Design
2. Generate ALL 9 sections as HTML. Fill in what you can infer; use professional placeholders for unknowns.
3. Mark sections as "draft" (has some content based on input) or "empty" (placeholder only).
4. Write a conversational reply with the FIRST clarifying question to improve the weakest section.
5. Skip sections not relevant to the archetype (mark as "complete" with "N/A" content).

IMPORTANT:
- Write from the perspective of a senior producer who understands video production deeply.
- Use industry-standard terminology.
- Be specific: instead of "various locations", write "2-3 indoor studio setups + 1 outdoor urban location".
- Budget: if not mentioned, suggest a realistic range based on the project scope.
- For unknown fields, write professional placeholders like "[To be confirmed - recommend discussing with talent agency]".

Respond with JSON:
{{
  "sections": {{
    "project_header": "<h2>1. Project Header</h2><ul><li>...</li></ul>",
    "budget_timeline": "...",
    ...
  }},
  "sections_status": {{
    "project_header": "draft",
    "budget_timeline": "empty",
    ...
  }},
  "archetypes": [2],
  "reply": "Your conversational message with the first clarifying question",
  "structured_data": {{
    "projectName": "...",
    "clientName": "...",
    "budget": null,
    "territory": [],
    "projectType": "..."
  }}
}}
"""
)

UPDATE_SYSTEM_PROMPT = (
    """You are AIVUS, an expert video production brief creation system.

The client is refining their brief through conversation. You received their latest answer.

{methodology_context}

{feedback_context}

Current sections being discussed:
{current_sections_html}

Current sections status:
{sections_status_json}

Already asked about: {questions_asked}

"""
    + SECTION_TEMPLATE
    + """

INSTRUCTIONS:
1. Update the relevant sections based on the client's answer.
2. Return ONLY the sections that changed (as section_patches).
3. Update sections_status for changed sections.
4. Determine the next most important question to ask (pick the weakest incomplete section).
5. If ALL important sections are "complete", set conversation_phase to "complete" and write a closing message.

Respond with JSON:
{{
  "section_patches": {{
    "scope_video": "<h2>5. Scope of Work: Video Production</h2>..."
  }},
  "sections_status": {{
    "scope_video": "complete"
  }},
  "reply": "Your response acknowledging their input + next question OR completion message",
  "conversation_phase": "questioning" or "complete",
  "structured_data_updates": {{}}
}}
"""
)

ANSWER_SYSTEM_PROMPT = """You are AIVUS, an expert video production brief assistant.

The client asked a question or made a comment that doesn't directly update the brief content.
Answer briefly and helpfully, then guide them back to completing the brief.

Current conversation phase: {conversation_phase}
Current sections needing work: {incomplete_sections}

Keep your response concise (2-4 sentences). Be professional and knowledgeable about video production.

Respond with JSON:
{{
  "reply": "Your helpful response"
}}
"""

ROUTER_SYSTEM_PROMPT = """You are a message classifier for a video production brief creation system.

Classify the user's message into one of these intents:
- "first_generation": This is the first message describing a new project (only if conversation_phase is "initial")
- "section_answer": The user is answering a question about specific brief sections or providing info that updates the brief
- "question_or_chat": The user is asking a question, saying thanks, or chatting without providing brief-relevant info

Context:
- conversation_phase: {conversation_phase}
- last_assistant_message: {last_assistant_message}

Respond with JSON:
{{
  "intent": "first_generation" | "section_answer" | "question_or_chat",
  "affected_sections": ["section_key1", ...] (only for section_answer, empty list otherwise)
}}

When in doubt, classify as "section_answer" (safest default).
"""

EXTRACT_SYSTEM_PROMPT = """You are a structured data extractor for video production briefs.

Given the brief sections (HTML), extract structured data for generating an offer/estimate.

Respond with JSON:
{{
  "projectName": "string",
  "clientName": "string",
  "brandName": "string",
  "description": "string",
  "projectType": "string",
  "budget": number or null,
  "territory": ["string"],
  "term": {{"length": "string", "unit": "string"}},
  "mainVideoDuration": {{"number": "1", "length": "30", "timeUnit": "sec"}},
  "cuts": [{{"number": "1", "length": "15", "timeUnit": "sec"}}],
  "shootingDays": {{"number": "string", "length": "string"}},
  "distributionAndAdPlacements": "string",
  "talentRequirements": "string",
  "locations": "string",
  "postProduction": "string",
  "deliverables": ["string"]
}}
"""


def _build_methodology_context(archetypes: list[int], sections: list[str]) -> str:
    from django.db.models import Q  # noqa: PLC0415

    from aivus_backend.projects.models import BriefMethodology  # noqa: PLC0415

    entries = (
        BriefMethodology.objects.filter(is_active=True)
        .filter(Q(archetype_code__isnull=True) | Q(archetype_code__in=archetypes))
        .filter(Q(section_key="") | Q(section_key__in=sections))
        .order_by("priority")
    )

    if not entries.exists():
        return ""

    parts = [f"### {x.title}\n{x.content}" for x in entries]
    return "METHODOLOGY:\n" + "\n\n".join(parts)


def _build_feedback_context(sections: list[str]) -> str:
    from django.db.models import Q  # noqa: PLC0415

    from aivus_backend.projects.models import BriefFeedback  # noqa: PLC0415

    negative_feedback = (
        BriefFeedback.objects.filter(rating="down")
        .filter(Q(section_key="") | Q(section_key__in=sections))
        .order_by("-created_at")[:15]
    )

    if not negative_feedback:
        return ""

    lines = [f"- {x.section_key or 'general'}: {x.comment}" for x in negative_feedback]
    return "KNOWN ISSUES TO AVOID:\n" + "\n".join(lines)


def _accumulate_tokens(state: BriefGraphState, response: LLMResponse) -> dict:
    return {
        "turn_input_tokens": state.get("turn_input_tokens", 0) + response.input_tokens,
        "turn_output_tokens": state.get("turn_output_tokens", 0)
        + response.output_tokens,
        "turn_cost_usd": state.get("turn_cost_usd", 0.0) + response.cost_usd,
        "model_used": response.model_used,
    }


def route_message(state: BriefGraphState) -> dict:
    conversation_phase = state.get("conversation_phase", "initial")

    if conversation_phase == "initial":
        return {"route_intent": "first_generation"}

    last_assistant = ""
    for msg in reversed(state.get("messages", [])):
        if msg.get("role") == "assistant":
            last_assistant = msg.get("content", "")[:200]
            break

    messages = [
        {
            "role": "system",
            "content": ROUTER_SYSTEM_PROMPT.format(
                conversation_phase=conversation_phase,
                last_assistant_message=last_assistant,
            ),
        },
        {
            "role": "user",
            "content": state["messages"][-1]["content"],
        },
    ]

    try:
        parsed, response = call_llm_json(
            model=MODEL_ROUTER,
            messages=messages,
            temperature=0.0,
            max_tokens=200,
        )
        intent = parsed.get("intent", "section_answer")
        affected = parsed.get("affected_sections", [])

        token_update = _accumulate_tokens(state, response)

        return {
            **token_update,
            "route_intent": intent,
            "sections_changed": affected,
        }
    except Exception:
        logger.exception("Router failed, defaulting to section_answer")
        return {"route_intent": "section_answer"}


def _route_decision(state: BriefGraphState) -> str:
    intent = state.get("route_intent", "section_answer")
    if intent == "first_generation":
        return "generate"
    if intent == "question_or_chat":
        return "answer"
    return "update"


def generate_full_brief(state: BriefGraphState) -> dict:
    user_message = state["messages"][-1]["content"]
    methodology = _build_methodology_context([], BRIEF_SECTION_KEYS)
    feedback = _build_feedback_context(BRIEF_SECTION_KEYS)

    messages = [
        {
            "role": "system",
            "content": GENERATE_SYSTEM_PROMPT.format(
                methodology_context=methodology,
                feedback_context=feedback,
            ),
        },
        {"role": "user", "content": user_message},
    ]

    parsed, response = call_llm_json(
        model=MODEL_GENERATION,
        messages=messages,
        temperature=0.7,
        max_tokens=6000,
    )

    sections = parsed.get("sections", {})
    sections = {k: v for k, v in sections.items() if k in BRIEF_SECTION_KEYS}
    sections = sanitize_sections(sections)
    sections_status = parsed.get("sections_status", {})
    sections_status = {
        k: v
        for k, v in sections_status.items()
        if k in BRIEF_SECTION_KEYS and v in ("empty", "draft", "complete")
    }
    archetypes = [
        x
        for x in parsed.get("archetypes", [])
        if isinstance(x, int) and 1 <= x <= MAX_ARCHETYPE_CODE
    ]
    reply = parsed.get("reply", "")
    structured_data = parsed.get("structured_data", {})

    for key in BRIEF_SECTION_KEYS:
        if key not in sections_status:
            sections_status[key] = "empty" if key not in sections else "draft"

    incomplete = [k for k, v in sections_status.items() if v != "complete"]

    token_update = _accumulate_tokens(state, response)

    return {
        **token_update,
        "document_sections": sections,
        "sections_status": sections_status,
        "archetypes": archetypes,
        "structured_data": structured_data,
        "conversation_phase": "questioning",
        "reply": reply,
        "sections_changed": list(sections.keys()),
        "section_patches": sections,
        "pending_sections": incomplete,
        "questions_asked": [],
        "messages": [{"role": "assistant", "content": reply}],
    }


def update_and_respond(state: BriefGraphState) -> dict:
    current_sections = state.get("document_sections", {})
    sections_status = state.get("sections_status", {})
    archetypes = state.get("archetypes", [])
    questions_asked = state.get("questions_asked", [])

    affected = state.get("sections_changed", [])
    if not affected:
        incomplete = [k for k, v in sections_status.items() if v != "complete"]
        affected = incomplete[:3] if incomplete else list(current_sections.keys())[:3]

    sections_html_parts = []
    for key in affected:
        html = current_sections.get(key, "")
        if html:
            label = SECTION_LABELS.get(key, key)
            sections_html_parts.append(f"[{label}]\n{html}")

    methodology = _build_methodology_context(archetypes, affected)
    feedback = _build_feedback_context(affected)

    history_messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in state.get("messages", [])[-10:]
    ]

    system_prompt = UPDATE_SYSTEM_PROMPT.format(
        methodology_context=methodology,
        feedback_context=feedback,
        current_sections_html="\n\n".join(sections_html_parts),
        sections_status_json=json.dumps(sections_status),
        questions_asked=", ".join(questions_asked) if questions_asked else "none yet",
    )

    messages = [{"role": "system", "content": system_prompt}, *history_messages]

    parsed, response = call_llm_json(
        model=MODEL_GENERATION,
        messages=messages,
        temperature=0.7,
        max_tokens=3000,
    )

    section_patches = parsed.get("section_patches", {})
    section_patches = {
        k: v for k, v in section_patches.items() if k in BRIEF_SECTION_KEYS
    }
    section_patches = sanitize_sections(section_patches)
    new_status = parsed.get("sections_status", {})
    new_status = {
        k: v
        for k, v in new_status.items()
        if k in BRIEF_SECTION_KEYS and v in ("empty", "draft", "complete")
    }
    reply = parsed.get("reply", "")
    new_phase = parsed.get("conversation_phase", "questioning")
    if new_phase not in ("initial", "questioning", "refining", "complete"):
        new_phase = "questioning"
    structured_updates = parsed.get("structured_data_updates", {})

    merged_sections = dict(current_sections)
    merged_sections.update(section_patches)

    merged_status = dict(sections_status)
    merged_status.update(new_status)

    merged_structured = dict(state.get("structured_data", {}))
    merged_structured.update(structured_updates)

    changed_keys = list(section_patches.keys())
    new_questions_asked = list(questions_asked)
    new_questions_asked.extend(changed_keys)

    token_update = _accumulate_tokens(state, response)

    return {
        **token_update,
        "document_sections": merged_sections,
        "sections_status": merged_status,
        "structured_data": merged_structured,
        "conversation_phase": new_phase,
        "reply": reply,
        "sections_changed": changed_keys,
        "section_patches": section_patches,
        "questions_asked": new_questions_asked,
        "messages": [{"role": "assistant", "content": reply}],
    }


def answer_or_chat(state: BriefGraphState) -> dict:
    user_message = state["messages"][-1]["content"]
    sections_status = state.get("sections_status", {})
    incomplete = [k for k, v in sections_status.items() if v != "complete"]

    messages = [
        {
            "role": "system",
            "content": ANSWER_SYSTEM_PROMPT.format(
                conversation_phase=state.get("conversation_phase", "questioning"),
                incomplete_sections=", ".join(incomplete) if incomplete else "none",
            ),
        },
        {"role": "user", "content": user_message},
    ]

    parsed, response = call_llm_json(
        model=MODEL_CHAT,
        messages=messages,
        temperature=0.7,
        max_tokens=500,
    )

    reply = parsed.get("reply", "")
    token_update = _accumulate_tokens(state, response)

    return {
        **token_update,
        "reply": reply,
        "sections_changed": [],
        "section_patches": {},
        "messages": [{"role": "assistant", "content": reply}],
    }


def extract_structured(state: BriefGraphState) -> dict:
    sections = state.get("document_sections", {})
    sections_text = "\n\n".join(
        f"[{SECTION_LABELS.get(k, k)}]\n{v}" for k, v in sections.items() if v
    )

    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": sections_text},
    ]

    parsed, response = call_llm_json(
        model=MODEL_CHAT,
        messages=messages,
        temperature=0.0,
        max_tokens=1000,
    )

    token_update = _accumulate_tokens(state, response)

    return {
        **token_update,
        "structured_data": parsed,
    }


def persist(state: BriefGraphState) -> dict:
    return {}


def _build_graph():
    builder = StateGraph(BriefGraphState)

    builder.add_node("route", route_message)
    builder.add_node("generate", generate_full_brief)
    builder.add_node("update", update_and_respond)
    builder.add_node("answer", answer_or_chat)
    builder.add_node("extract", extract_structured)
    builder.add_node("persist", persist)

    builder.set_entry_point("route")
    builder.add_conditional_edges(
        "route",
        _route_decision,
        {
            "generate": "generate",
            "update": "update",
            "answer": "answer",
        },
    )

    builder.add_edge("generate", "persist")
    builder.add_edge("update", "persist")
    builder.add_edge("answer", "persist")
    builder.add_edge("persist", END)

    return builder.compile()


_graph = None


def _get_graph():
    global _graph  # noqa: PLW0603
    if _graph is None:
        _graph = _build_graph()
    return _graph


def process_brief_message(  # noqa: PLR0913
    user_message: str,
    brief_id: str,
    document_sections: dict | None = None,
    sections_status: dict | None = None,
    archetypes: list[int] | None = None,
    structured_data: dict | None = None,
    conversation_phase: str = "initial",
    questions_asked: list[str] | None = None,
    history: list | None = None,
) -> dict:
    graph = _get_graph()

    messages: list[dict[str, str]] = []
    if history:
        messages.extend(
            {"role": msg["role"], "content": msg["content"]}
            for msg in history[-20:]
            if isinstance(msg, dict)
            and msg.get("role") in ("user", "assistant")
            and isinstance(msg.get("content"), str)
        )
    else:
        messages.append({"role": "user", "content": user_message})

    initial_state: BriefGraphState = {
        "messages": messages,
        "brief_id": brief_id,
        "document_sections": document_sections or {},
        "structured_data": structured_data or {},
        "archetypes": archetypes or [],
        "sections_status": sections_status
        or dict.fromkeys(BRIEF_SECTION_KEYS, "empty"),
        "pending_sections": [],
        "conversation_phase": conversation_phase,
        "questions_asked": questions_asked or [],
        "current_question_section": "",
        "methodology_context": "",
        "feedback_context": "",
        "reply": "",
        "sections_changed": [],
        "section_patches": {},
        "turn_input_tokens": 0,
        "turn_output_tokens": 0,
        "turn_cost_usd": 0.0,
        "model_used": "",
        "route_intent": "",
    }

    result = graph.invoke(initial_state)

    return {
        "reply": result.get("reply", ""),
        "document_sections": result.get("document_sections", {}),
        "section_patches": result.get("section_patches", {}),
        "sections_changed": result.get("sections_changed", []),
        "sections_status": result.get("sections_status", {}),
        "archetypes": result.get("archetypes", []),
        "structured_data": result.get("structured_data", {}),
        "conversation_phase": result.get("conversation_phase", "questioning"),
        "questions_asked": result.get("questions_asked", []),
        "input_tokens": result.get("turn_input_tokens", 0),
        "output_tokens": result.get("turn_output_tokens", 0),
        "cost_usd": result.get("turn_cost_usd", 0.0),
        "model_used": result.get("model_used", ""),
    }


def finalize_brief(
    brief_id: str,
    document_sections: dict,
) -> dict:
    _get_graph()

    state: BriefGraphState = {
        "messages": [],
        "brief_id": brief_id,
        "document_sections": document_sections,
        "structured_data": {},
        "archetypes": [],
        "sections_status": {},
        "pending_sections": [],
        "conversation_phase": "complete",
        "questions_asked": [],
        "current_question_section": "",
        "methodology_context": "",
        "feedback_context": "",
        "reply": "",
        "sections_changed": [],
        "section_patches": {},
        "turn_input_tokens": 0,
        "turn_output_tokens": 0,
        "turn_cost_usd": 0.0,
        "model_used": "",
        "route_intent": "",
    }

    result = extract_structured(state)
    return {
        "structured_data": result.get("structured_data", {}),
        "input_tokens": result.get("turn_input_tokens", 0),
        "output_tokens": result.get("turn_output_tokens", 0),
        "cost_usd": result.get("turn_cost_usd", 0.0),
        "model_used": result.get("model_used", ""),
    }
