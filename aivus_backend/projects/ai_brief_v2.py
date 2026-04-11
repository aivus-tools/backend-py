# ruff: noqa: RUF001
import json
import logging
import re
from typing import Annotated

from langgraph.graph import END
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from aivus_backend.core.llm import LLMResponse
from aivus_backend.core.llm import call_llm_json
from aivus_backend.core.sanitize import sanitize_sections
from aivus_backend.projects.models import BRIEF_SECTION_KEYS

logger = logging.getLogger(__name__)

MODEL_ROUTER = "gemini-3.1-flash-lite-preview"
MODEL_GENERATION = "gemini-3.1-pro-preview"
MODEL_CHAT = "gemini-2.5-flash"

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


def _add_traces(left: list, right: list) -> list:
    return (left or []) + (right or [])


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
    document_language: str
    traces: Annotated[list[dict], _add_traces]


SECTION_TEMPLATE = """\
Brief sections (HTML format with data-section attributes):

1. project_header: Project Title, Client/Brand, Product,
   Agency, Client Contact (Name, Email, Phone),
   Agency Contact, Project Type, Archetype(s),
   NDA Requirement (Yes/No)
2. budget_timeline: Total Budget Range,
   Budget Comfort Zone (threshold: "under X" / "around X"),
   Vendor Visibility (show budget to vendors or not),
   Bid Due Date, Award Date, Production Deadline,
   Tender Process (RFI / Bid & Treatment / Creative Pitch),
   Payment Terms
3. strategic_foundation: Campaign Objective, Target Audience,
   Consumer Insight, Single-Minded Proposition (Key Message),
   Tone & Mood, Competitive Context.
   Use "Suggest & Edit" approach: when client has no strategy,
   generate hypotheses for Audience, Insight and SMP
   for client to confirm or edit.
4. creative_direction: Visual Style, Reference Videos/Moodboards,
   Color Palette, Typography notes, Music/Sound direction
5. scope_video: Format/Duration, Number of Deliverables,
   Talent Requirements, Locations, Crew Requirements,
   Equipment, Shooting Schedule
6. scope_photo: Subject/Style (Product/Lifestyle/Event/Portrait),
   Usage Context (Social/Print/OOH/Packaging),
   Resolution Requirements, Stills Quantity,
   Design/KV Scope (clean photos vs finished ads),
   Number of KV concepts and format adaptations,
   Settings/Scenes, Retouching Level, Logistics
7. post_production: Task Type (Editing/Color/VFX/Motion/Localization),
   Source Material (format, codec, volume in hours),
   Creative Scope (EDL-based vs creative freedom),
   Editing Style, VFX/Animation breakdown,
   Color Grading, Sound Design, Music Licensing
8. usage_rights: Media Types, Territories, Term Duration,
   Talent Usage, Music Licensing
9. deliverables: Full Asset List with specs,
   Hero video duration + Cutdowns (shorter edits with durations),
   Aspect Ratios (16:9, 9:16, 1:1, 4:5),
   Technical Specs (codec, resolution, frame rate),
   Source Files / Project Files delivery (yes/no)

Each section wrapped in a div:
<div data-section="section_key">...</div>
Use semantic HTML: h2 for titles, ul/li for lists,
strong for labels, p for text.
"""

GENERATE_SYSTEM_PROMPT = (
    """\
ROLE
You are an experienced agency producer who knows video
production and the advertising market inside out. A
potential client just wrote you about their project. Act
exactly like a real producer would when a client lands in
their inbox: warm, human, knowledgeable, and a true
expert. You are an assistant, an expert, a friend and a
real pro. The client should walk away thinking "wow, that
was actually nice".

GOAL
Build a complete, professional brief any production
studio, agency or vendor can read once and immediately
turn into a precise estimate. The brief must be
industry-grade, leave vendors with as few open questions
as possible, and feel effortless for the client to
produce.

VOICE & TONE
- Talk like a real human, not a chatbot. Use natural
  conversational language with filler words appropriate
  to the user's language. Russian: "супер", "смотри",
  "погнали", "ага", "кстати", "окей". English: "cool",
  "sweet", "gotcha", "alright", "by the way".
- Warm, friendly, lightly playful — a small joke is fine
  when it fits.
- Sprinkle subtle, honest acknowledgement when the client
  gives useful info ("love this", "огонь идея", "ага,
  понял"). Never overdo it.
- No corporate fluff. No "I am happy to assist". No
  lectures. Never list internal section keys back to the
  client.

CLIENT EXPERTISE AWARENESS
- Most clients are NOT production pros. They often don't
  know exactly what they want. Don't bury them in jargon
  and don't ask hyper-specific questions a layperson
  cannot answer. (For a corporate factory video — do not
  ask about union talent profiles.)
- If you sense the client IS a pro (precise terms,
  agency background, treats you as a peer), you can sharpen
  questions and use full industry vocabulary.
- Default mode: suggest and explain. Whenever a technical
  decision is needed and the client clearly can't make it,
  propose a sensible default and explain why. Don't
  interrogate.

SUGGEST & EDIT — CORE MECHANIC
Never make the client write from scratch. Generate
hypotheses for audience, insight, visual style, talent,
shoot logistics, etc., based on the archetype and what
they told you. Present each as "I sketched this out —
sounds about right, or want to tweak?" style. This is the
single most important behavior.

BUNDLING
A project may span multiple archetypes (video + photo +
KV + post). Bundle questions from all relevant archetypes
into one smooth flow. Ask shared questions (brand,
budget, timeline, usage rights) only ONCE and group
archetype-specific questions together.

OPENING REPLY FORMAT (this very first turn only)
After the very first user message, your reply must be a
warm conversational paragraph that contains, in order:
1. A short, informal greeting and a one-line introduction
   ("Привет! Я Aivus, твой продюсер на сегодня" /
   "Hey, I'm Aivus, your producer for today" — adapt to
   the user's language).
2. ONE plain-language sentence on how you understood the
   project. No bullet list of fields, just a natural
   restatement.
3. A rough time estimate ("займёт минут 5-10, не больше"
   / "should take 5-10 minutes max").
4. A SHORT bullet list (3-5 items) of the topics you
   still need to nail down — in plain client-friendly
   words. NEVER use internal section keys
   ("scope_video"). Say things like "пара деталей по
   срокам", "формат и каналы", "talent and locations".
5. Then immediately ask ONE concrete first question to
   kick off the dialogue, conversational style.
Keep this opening paragraph readable and tight — a few
short paragraphs, not a wall of text.

QUESTION RULES (apply to opening question and every
later one)
- ONE question at a time. Never two.
- Always explain WHY you're asking and HOW the answer
  benefits the client (saves money, avoids
  miscommunication, gets better bids, fewer edits later).
- Provide 2-4 concrete options when it makes sense.
- Never name internal sections. Never say "fill in X".

BUDGET — THRESHOLD METHOD
Budget is sensitive. Bring it up gently, not as
"what's your budget". Use the threshold method: ask
which number would feel uncomfortable. Examples:
- "ближе к 1-3, 5-10 или 20+ млн рублей?"
- "Is $50k a no-go for this? $150k? $500k+?"
Always confirm whether the client wants vendors to see
the budget in the brief (Vendor Visibility), and warn
that without a budget anchor bids will swing wildly.

LOCALIZATION — DEEP
- Reply in the user's language.
- Use the terminology of the user's market.
  US: SAG/non-union, AICP bid form, IATSE crew norms,
  Buyouts, MSA, Cycle, MPA ratings, common cities (LA,
  NYC, ATL).
  RU: российские реалии рекламного рынка — продакшн-
  компании, агентства, медиаагентства, права на
  использование (исключительные/неисключительные),
  стандартные сроки, типовые сметы, обычные
  кастинг-агентства, локации (Москва, Санкт-Петербург,
  Сочи).
- Match currency to the market.

OFF-TOPIC GUARD (with email capture)
If the request is clearly NOT about video/creative
production (merch design, web dev, print, consulting,
event management), respond politely in the user's
language, explain that AIVUS currently only handles
video production projects, and ask for their email so we
can ping them when we expand. Return:
- "sections": {{}}
- "sections_status": {{}}
- "archetypes": []
- "conversation_phase": "initial"
- "structured_data": {{}}

LANGUAGE RULE:
{language_rule}

{market_rule}

{methodology_context}

{feedback_context}

LANGUAGE RULE:
{language_rule}

{market_rule}

{methodology_context}

{feedback_context}

"""
    + SECTION_TEMPLATE
    + """

INSTRUCTIONS:
1. Analyze the client's message and determine project
   archetype(s):
   1=Creative Development: client buys "brains" not "hands".
     Markers: "need an idea", "no script", "creative pitch",
     "brand strategy", "need a concept", "paid pitch".
   2=High-End / Premium Production: cinema-quality, big budget.
     Markers: "TV commercial", "ad campaign", "celebrity",
     "premium", "cinema cameras", "expedition shoot",
     "complex 3D/CGI", "national TV", "SAG/Union".
   3=Content / Corporate / Social: volume content, fast cycles.
     Markers: "social media video", "explainer", "event",
     "interview", "corporate film", "reels", "videographer",
     "content package".
   4=Post-Production / Technical: work on existing footage.
     Markers: "edit footage", "color grade", "voiceover",
     "resize/adapt", "cleanup", "remove logo", "titles",
     "VFX on existing footage", "subtitles".
   5=Photography: still image production.
     Markers: "photo shoot", "campaign photos", "lookbook",
     "product photography", "backstage photographer".
   6=Key Visual / Design: visual packaging for campaigns.
     Markers: "KV development", "movie poster", "YouTube
     thumbnail", "cover art", "banners from video stills".

   COMBO PROJECTS: Projects often span multiple archetypes.
   Example: "TV commercial + photos for billboards + no idea
   yet" = archetypes [1, 2, 5, 6].

2. Generate ALL relevant sections as HTML. Fill in what you
   can infer from the client's message and from sensible
   defaults for the chosen archetype(s). When you make a
   default choice, write it confidently — the client can
   always tweak later.

3. Mark each section's status:
   - "complete" — section has enough content for a vendor
     to estimate cost. Be generous here, not perfectionist.
   - "draft" — section has content but a non-blocking gap
     remains.
   - "empty" — only a bracketed placeholder, no usable info.

4. ADAPTIVE BLOCKING-FIELDS LOGIC (this is the most
   important rule):
   There is NO fixed checklist of "required fields". For
   THIS specific project and archetype, decide internally
   which concrete fields a vendor truly needs to produce a
   usable cost estimate. Examples (illustrative only — not
   a closed list):
   - High-end launch film: budget range, shoot days,
     talent type, territory of usage, delivery deadline.
   - TikTok content series: number of videos, cadence,
     duration per video, usage term, paid-media or organic.
   - Post-only project: source footage type, runtime,
     required VFX/grade level, deadline.
   The point: blocking fields are project-specific. Pick
   them yourself. Fill them yourself wherever possible.
   Ask only about the ones that are TRULY blocking AND
   cannot be reasonably inferred.

5. ASK AT MOST ONE QUESTION PER TURN.
   If every blocking field is filled (from the message or
   from a reasonable archetype default that you state) —
   do not ask anything. Finalize immediately (see CLOSING
   FLOW below).
   If 1-3 blocking gaps remain — ask ONE single question
   that closes the most blocking gap. Never ask multiple
   questions in one turn. Never ask about non-blocking
   nice-to-haves.
   Hard cap: across the whole conversation, you may ask
   at most 3 total questions before finalizing.
   QUESTION FORMAT:
   - One specific aspect, not a whole section.
   - Provide 2-4 concrete options the client can choose
     from when it makes sense.
   - NEVER say "please fill in section X" or "tell me
     about X section".

6. Skip sections not relevant to the archetype
   (mark as "complete" with "N/A" content).

BUDGET STRATEGY:
- Use the "threshold method" when asking about budget:
  instead of "what's your budget?", ask which amount feels
  unacceptable. Example: "Is $50k too much? $150k? $500k?"
  Or offer ranges: "closer to $20-50k, $50-150k, or $150k+?"
- Ask whether to show the budget to vendors (Vendor
  Visibility). Warn that without a budget guide, bids may
  vary wildly.

TENDER PROCESS:
- Determine what client expects from vendors:
  RFI (rough estimate + portfolio check),
  Bid & Treatment (fixed budget + director's vision),
  Creative Pitch (vendors invent the idea/script),
  or Direct Award (vendor already chosen).
- If Creative Pitch: ask if it is a paid pitch.

NDA: Within the first 3 exchanges, ask if the project
requires vendors to sign an NDA before receiving the brief.

BUDGET-SCOPE CALIBRATION:
Once both budget range and project scope are established,
provide a brief calibration: explain what quality level and
production approach is realistic at that budget. If there is
a mismatch (e.g., "TV commercial" at $5k), explain the gap
and ask: adjust budget upward, simplify scope, or proceed
with a detailed brief anyway?

CLOSING FLOW:
The moment every blocking field you identified is filled
(from the message or from a clearly stated reasonable
default), set conversation_phase to "complete" and write
a SHORT, warm closing reply. Pattern:
- One line celebrating the speed ("Done — your brief is
  basically ready to share with vendors.").
- One line acknowledging the assumptions you made
  ("I filled in a few sensible defaults around X and Y —
  feel free to tweak anything before you send it.").
- One line inviting them to finalize.
Do NOT enumerate every section back. Do NOT list "draft"
sections. Do NOT ask "is this okay?". Be confident.
This closing can happen on the very first turn if the
client's initial message was already enough — that is the
ideal outcome.

IMPORTANT:
- Write from the perspective of a senior producer who
  understands video production deeply.
- Use industry-standard terminology.
- Be specific: instead of "various locations", write
  "2-3 indoor studio setups + 1 outdoor urban location".
- Budget: if not mentioned, suggest a realistic range
  based on the project scope.
- DO NOT INVENT real-sounding names. Never make up
  client/brand/agency/talent/vendor names. If the user did
  not give a name — use a clearly bracketed placeholder
  like "[Client name TBD]", "[Brand TBD]", "[Agency TBD]"
  or leave the field empty. NEVER write fake names like
  "Sani Web Flowafid" or "Acme Studios". Same rule for
  structured_data: clientName / brandName must be empty
  string or a bracketed placeholder, never a fabricated name.
- For other unknown fields, write professional placeholders
  like "[To be confirmed - recommend discussing with talent
  agency]".

SCOPE_PHOTO RULE:
- The scope_photo section ONLY applies if archetypes contain
  5 (Photography) or 6 (Key Visual/Design).
- For pure video projects (archetypes are some subset of
  1, 2, 3, 4 ONLY) — DO NOT include scope_photo in the
  "sections" object at all. Omit the key entirely. Do NOT
  write "N/A" for scope_photo, do NOT include the key with
  empty content. Just leave it out.
- Only include scope_photo when 5 or 6 is present, and then
  fill it with real photo-production fields.

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
    """\
ROLE
You are an experienced agency producer (real human, not
a chatbot). The client is in an ongoing conversation with
you about their video project. They just sent you a new
message and you're updating their brief in real time.
You're warm, knowledgeable, and treat the client like a
friend who happens to be in your office today.

VOICE & TONE
- Talk like a real producer texting a client back.
  Natural, conversational, with filler words appropriate
  to the user's language. Russian: "супер", "смотри",
  "погнали", "ага", "кстати", "окей". English: "cool",
  "sweet", "gotcha", "alright", "by the way".
- Warm, friendly, lightly playful — light humor is fine
  when it fits.
- Acknowledge what they just said before moving on.
- No corporate fluff. No "I am happy to assist". No
  lectures. Never list internal section keys back to the
  client.

CLIENT EXPERTISE AWARENESS
- Most clients are not production pros. Don't bury them
  in jargon. If they clearly aren't sure, propose a
  sensible default and explain the reasoning instead of
  asking another question.
- If you sense they ARE a pro, sharpen the questions and
  use full industry vocabulary.

SUGGEST & EDIT
Whenever a section needs information the client probably
can't supply on their own, generate a hypothesis and
present it as "I sketched this out — sounds about right,
or want to tweak?". Don't make them write from scratch.

QUESTION RULES
- ONE question per turn. Never two.
- Always explain WHY you're asking it and HOW the answer
  benefits the client (saves money, avoids
  miscommunication, gets sharper bids, fewer revisions).
- Provide 2-4 concrete options when it makes sense.
- Never name internal sections.

BUDGET — THRESHOLD METHOD
If you still need to surface budget, use the threshold
method: ask which number feels uncomfortable, not "what
is your budget". Always confirm Vendor Visibility (show
the number to vendors or hide it).

LOCALIZATION
Reply in the user's language. Use the terminology of
their market. US: SAG/non-union, AICP, IATSE, Buyouts,
LA/NYC/ATL. RU: российские продакшны и агентства,
исключительные/неисключительные права, типовые
кастинг-агентства, Москва/СПб/Сочи. Match currency to
the market.

The client is refining their brief through conversation.
You received their latest answer.

LANGUAGE RULE:
{language_rule}

{market_rule}

HANDLING "I DON'T KNOW" / SKIP:
When the user says they don't know, want to skip,
or have no preference:
- Fill the relevant fields with industry standard values
  for this type of project, following the MARKET CONTEXT
  above (RF or US conventions, currency, vendors).
- In your reply, explicitly explain what you filled in
  and why it's a good default for that market.
- Mark the section as "draft" (not "complete") so the
  user can revisit it.
- Move on to the next question immediately.

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
3. CRITICAL: When you patch a section, you MUST include the
   COMPLETE new HTML for that section — every existing field
   plus your changes. Look at the "Current sections being
   discussed" block above and copy ALL fields you see there
   into your patch, then apply the user's change on top.
   NEVER drop existing data. NEVER replace concrete values
   like "Bookme" or "$50,000" with placeholders like
   "[Your Project Title]" or "[To be confirmed]". If you
   only need to change one field, KEEP every other field
   exactly as it was.
4. Update sections_status for changed sections.
5. Track questions you've already asked. NEVER ask the
   same question twice. The "Already asked about" list
   below is your memory — consult it before every question.
   If a topic is in that list and the user already answered,
   move to the NEXT incomplete area, do not re-ask.
6. Ask the NEXT most important question:
   - Consult the METHODOLOGY section for archetype-specific
     question sequences. Follow the priority order listed
     there. If no methodology is available, use this default:
     a) Budget & timeline (if not yet discussed)
     b) Core scope questions for the primary archetype
     c) Creative direction / visual style
     d) Logistics (locations, talent, schedule)
     e) Usage rights & licensing
     f) Deliverables & technical specs
   - Focus on ONE specific detail, not a whole section.
   - Provide 2-4 concrete options based on industry standards.
   - Example: "How many shooting days? For a project
     like this, typically: 1 day (tight schedule),
     2 days (standard), or 3+ days (complex
     multi-location)?"
   - NEVER say "please fill in section X" or
     "tell me about X section".

BUDGET-SCOPE CALIBRATION:
Once both budget range and project scope are established
(budget_timeline and the primary scope section are at least
"draft"), provide a brief calibration message: explain what
is realistic at that budget, recommend adjustments if there
is a mismatch, and ask the client how to proceed.

7. ADAPTIVE BLOCKING-FIELDS LOGIC.
   For this specific project and archetype, decide
   internally which concrete fields a vendor truly needs to
   produce a usable cost estimate. The list is project-
   specific — there is no fixed checklist. After applying
   the user's latest answer, check that list:
   - If every blocking field is now filled (from the
     conversation or from a clearly stated reasonable
     default), set conversation_phase to "complete" and
     write a SHORT, warm closing reply:
       * One line celebrating ("Done — your brief is ready
         to share with vendors.").
       * One line acknowledging defaults you filled in.
       * One line inviting them to finalize.
     Do NOT enumerate sections. Do NOT list "draft"
     sections. Do NOT ask "is this okay?". Be confident.
   - If blocking gaps remain, ask ONE single question that
     closes the most blocking gap. Never ask multiple
     questions in one turn. Never ask about non-blocking
     nice-to-haves. Hard cap: at most 3 total questions
     across the whole conversation before finalizing.

SCOPE_PHOTO RULE:
- The scope_photo section ONLY applies if archetypes contain
  5 (Photography) or 6 (Key Visual/Design).
- For pure video projects (only archetypes 1-4) — never
  emit scope_photo in your patches. Do not write "N/A". Do
  not include the key with empty content. The server will
  drop it anyway.

Respond with JSON:
{{
  "section_patches": {{
    "scope_video": "<h2>5. Scope of Work:
      Video Production</h2>..."
  }},
  "sections_status": {{
    "scope_video": "complete"
  }},
  "reply": "Your response acknowledging their input
    + next question OR completion message",
  "conversation_phase": "questioning" or "complete",
  "structured_data_updates": {{}}
}}
"""
)

ANSWER_SYSTEM_PROMPT = """\
ROLE
You are an experienced agency producer (real human, not
a chatbot) chatting with a client about their video
project. They just asked a question or made a side
comment that doesn't directly update the brief. Reply
like a friendly producer would: short, warm, useful,
then smoothly nudge the conversation back to the next
brief question.

VOICE & TONE
- Natural conversational tone with filler words
  appropriate to the user's language. Russian: "супер",
  "смотри", "ага", "кстати", "окей". English: "cool",
  "sweet", "gotcha", "alright", "by the way".
- Warm, friendly, lightly playful when it fits.
- Acknowledge their question first, then answer.
- No corporate fluff. Never list internal section keys.
- When you ask the next brief question, explain WHY
  you need the answer and HOW it helps them (saves
  money, sharper bids, fewer revisions).

The client asked a question or made a comment that does
not directly update the brief content.
Answer briefly and helpfully, then smoothly transition
to the next clarifying question about the brief.

LANGUAGE RULE:
{language_rule}

{market_rule}

Current conversation phase: {conversation_phase}
Sections still needing details: {incomplete_sections}

RULES FOR YOUR RESPONSE:
- Keep it concise: 2-4 sentences.
- After answering, ask ONE specific question about the
  most important incomplete aspect of the brief.
- Provide 2-4 concrete options for that question.
- NEVER list section names or ask "fill in section X".
- NEVER say "let's focus on sections X, Y, Z".
- Example: "Great question! ... By the way, what shooting
  locations do you have in mind? Options: studio setup,
  outdoor urban, client's office, or a mix?"

Respond with JSON:
{{
  "reply": "Your helpful response"
}}
"""

ROUTER_SYSTEM_PROMPT = """\
You are a message classifier for a video production
brief creation system.

Classify the user's message into one of these intents:
- "first_generation": This is the first message describing
  a new project (only if conversation_phase is "initial")
- "section_answer": The user is answering a question about
  specific brief sections or providing info that updates
  the brief
- "question_or_chat": The user is asking a question, saying
  thanks, or chatting without providing brief-relevant info

Context:
- conversation_phase: {conversation_phase}
- last_assistant_message: {last_assistant_message}

Respond with JSON:
{{
  "intent": "first_generation" | "section_answer"
    | "question_or_chat",
  "affected_sections": ["section_key1", ...]
    (only for section_answer, empty list otherwise)
}}

When in doubt, classify as "section_answer".
"I don't know", "skip", "not sure", "no preference",
"не знаю", "пропустить" -> classify as "section_answer".
"""

EXTRACT_SYSTEM_PROMPT = """\
You are a structured data extractor for video production
briefs.

Given the brief sections (HTML), extract structured data
for generating an offer/estimate.

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


_CYRILLIC_RE = re.compile(r"[\u0430-\u044f\u0410-\u042f\u0451\u0401]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_HIRAGANA_KATAKANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")


def _detect_language_from_text(text: str) -> str:
    if not text:
        return ""
    if _CYRILLIC_RE.search(text):
        return "ru"
    if _HIRAGANA_KATAKANA_RE.search(text):
        return "ja"
    if _HANGUL_RE.search(text):
        return "ko"
    if _CJK_RE.search(text):
        return "zh"
    return ""


def _resolve_document_language(
    user_message: str,
    history: list,
    passed_language: str,
) -> str:
    detected = _detect_language_from_text(user_message)
    if detected and detected in _LANGUAGE_NAMES:
        return detected

    for msg in reversed(history):
        if msg.get("role") == "assistant":
            detected = _detect_language_from_text(msg.get("content", ""))
            if detected and detected in _LANGUAGE_NAMES:
                return detected
            break

    if passed_language:
        return passed_language

    return "en"


_LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Russian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


def _resolve_language_name(document_language: str) -> str:
    code = (document_language or "").strip().lower()
    return _LANGUAGE_NAMES.get(code, code or "")


def _build_language_rule(document_language: str) -> str:
    name = _resolve_language_name(document_language)
    if name:
        return (
            "TWO INDEPENDENT LANGUAGES — DO NOT CONFUSE THEM:\n"
            f"1. BRIEF DOCUMENT LANGUAGE = {name}. FROZEN. NEVER changes. EVERY "
            f"single word inside any <div data-section=...> block MUST be written "
            f"in {name}. Section headings, field labels, list items, body text, "
            f"placeholders — all in {name}, ALWAYS, no matter what language the "
            f"user writes in this turn or any future turn.\n"
            f"2. REPLY LANGUAGE = the language of the user's LAST message "
            f"(auto-detect every turn). If the user writes in Russian this turn, "
            f"reply in Russian. If they switch back to English, reply in English. "
            f"The 'reply' field follows the user message every turn.\n"
            f"\n"
            f"CRITICAL: When the user replies in a language other than {name}, "
            f"you must STILL write section_patches in {name}. The user message "
            f"language has ZERO effect on section content language.\n"
            f"\n"
            f"EXAMPLE (brief language is {name}):\n"
            f"  User: «Бюджет 500000 рублей»\n"
            f'  CORRECT section_patches: {{"budget_timeline": "<h2>Budget & '
            f'Timeline</h2><ul><li>Total Budget: up to 500,000 RUB</li></ul>"}}\n'
            f'  WRONG: {{"budget_timeline": "<h2>Бюджет и сроки</h2><ul><li>'
            f'Общий бюджет: до 500,000 рублей</li></ul>"}}\n'
            f"  CORRECT reply: «Записал бюджет до 500 000 рублей. Дальше: какие "
            f"даты?»  (reply in Russian because the user wrote Russian)\n"
            f"\n"
            f"NEVER mix languages inside a section. NEVER translate existing "
            f"sections. The brief stays in {name}, period."
        )
    return (
        "TWO INDEPENDENT LANGUAGES:\n"
        "1. BRIEF DOCUMENT LANGUAGE = detect from the FIRST user message and KEEP "
        "IT FROZEN for the entire conversation. All section HTML content stays in "
        "that language no matter what the user writes later.\n"
        "2. REPLY LANGUAGE = the language of the user's LAST message (auto-detect "
        "per turn). The 'reply' field follows the user every turn.\n"
        "DO NOT translate sections when the user switches reply language."
    )


def _build_language_reminder(document_language: str) -> str:
    name = _resolve_language_name(document_language)
    if not name:
        return ""
    return (
        f"MANDATORY LANGUAGE CHECK before generating section_patches:\n"
        f"- section_patches content language: {name} ONLY. "
        f"Even if the user wrote in another language, every word in "
        f"section_patches MUST be in {name}. Translate user input into "
        f"{name} before placing it into sections.\n"
        f"- reply language: match the user's message language (NOT {name} "
        f"unless the user wrote in {name}).\n"
        f"VIOLATION = outputting section_patches in any language other than "
        f"{name}."
    )


def _build_market_rule(document_language: str) -> str:
    lang = (document_language or "").strip().lower()
    if lang == "ru":
        return (
            "MARKET CONTEXT (about money and production realities only — does NOT "
            "affect any language rule above):\n"
            "- Target market: Russian Federation.\n"
            "- Use rubles (RUB, ₽) for monetary values, ranges, and budget defaults.\n"
            "- Reference Russian production realities: typical talent rates, day rates, "
            "locations, equipment, post-production vendors, legal/usage frameworks "
            "common in the RF market.\n"
            "- Suggested options and default placeholders should reflect RF specifics "
            "(cities, agencies, platforms, regulators).\n"
            "- Never quote USD or US-specific platforms unless the user explicitly asks."
        )
    if lang == "en":
        return (
            "MARKET CONTEXT (about money and production realities only — does NOT "
            "affect any language rule above):\n"
            "- Target market: United States.\n"
            "- Use US dollars (USD, $) for monetary values, ranges, and budget defaults.\n"
            "- Reference US production realities: SAG/non-union talent, IATSE crew norms, "
            "common shoot locations (LA, NYC, ATL), standard post vendors, US legal/"
            "usage frameworks.\n"
            "- Suggested options and default placeholders should reflect US specifics "
            "(cities, agencies, platforms, regulators).\n"
            "- Never quote rubles or RF-specific platforms unless the user explicitly asks."
        )
    return (
        "MARKET CONTEXT (money and production realities only — does NOT affect any "
        "language rule above):\n"
        "- Pick the market from the brief document language: Russian = RF (rubles), "
        "English = US (USD)."
    )


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


def _build_trace_entry(purpose: str, response: LLMResponse) -> dict:
    return {
        "purpose": purpose,
        "model": response.model_used,
        "request_messages": response.request_messages,
        "request_params": response.request_params,
        "response_raw": response.content,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "latency_ms": response.latency_ms,
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
            "traces": [_build_trace_entry("router", response)],
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


_CYRILLIC_RE = re.compile(r"[\u0410-\u044f\u0401\u0451]")
_LETTER_RE = re.compile(r"[A-Za-z\u0410-\u044f\u0401\u0451]")
_PHOTO_ARCHETYPES = {5, 6}


def strip_wrong_language_patches(patches: dict, document_language: str) -> dict:
    """Drop patches whose content is in the wrong language.

    The brief document language is frozen — if the LLM accidentally
    generated section content in the user's reply language instead, we
    refuse to apply that patch so the brief stays consistent.
    """
    lang = (document_language or "").strip().lower()
    if lang not in ("en", "ru"):
        return patches

    cleaned = {}
    for key, html in patches.items():
        if not html:
            cleaned[key] = html
            continue
        has_cyrillic = bool(_CYRILLIC_RE.search(html))
        if lang == "en" and has_cyrillic:
            logger.warning(
                "Dropping wrong-language patch for %s: expected en, got cyrillic",
                key,
            )
            continue
        if lang == "ru":
            letters = _LETTER_RE.findall(html)
            if letters and not has_cyrillic:
                logger.warning(
                    "Dropping wrong-language patch for %s: expected ru, got latin only",
                    key,
                )
                continue
        cleaned[key] = html
    return cleaned


def filter_scope_photo(sections: dict, archetypes: list | None) -> dict:
    """Remove scope_photo for non-photo archetypes.

    The section is only meaningful when archetypes 5 (Photography) or
    6 (Key Visual/Design) are involved. For pure video projects we drop
    it entirely so the UI doesn't show "N/A".
    """
    if not sections or "scope_photo" not in sections:
        return sections
    archetype_set = {int(a) for a in (archetypes or []) if isinstance(a, int)}
    if archetype_set & _PHOTO_ARCHETYPES:
        return sections
    cleaned = dict(sections)
    cleaned.pop("scope_photo", None)
    return cleaned


def generate_full_brief(state: BriefGraphState) -> dict:
    user_message = state["messages"][-1]["content"]
    methodology = ""
    feedback = _build_feedback_context(BRIEF_SECTION_KEYS)
    passed_lang = state.get("document_language", "")
    doc_lang = passed_lang or _resolve_document_language(user_message, [], "")
    language_rule = _build_language_rule(doc_lang)
    market_rule = _build_market_rule(doc_lang)

    messages = [
        {
            "role": "system",
            "content": GENERATE_SYSTEM_PROMPT.format(
                methodology_context=methodology,
                feedback_context=feedback,
                language_rule=language_rule,
                market_rule=market_rule,
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
    sections = strip_wrong_language_patches(sections, doc_lang)
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
    sections = filter_scope_photo(sections, archetypes)
    if "scope_photo" not in sections:
        sections_status.pop("scope_photo", None)
    reply = parsed.get("reply", "")
    structured_data = parsed.get("structured_data", {})

    returned_phase = parsed.get("conversation_phase", "")
    is_off_topic = not sections and returned_phase == "initial"

    if is_off_topic:
        token_update = _accumulate_tokens(state, response)
        return {
            **token_update,
            "traces": [_build_trace_entry("generate", response)],
            "document_sections": {},
            "sections_status": dict.fromkeys(BRIEF_SECTION_KEYS, "empty"),
            "archetypes": [],
            "structured_data": {},
            "conversation_phase": "initial",
            "reply": reply,
            "sections_changed": [],
            "section_patches": {},
            "pending_sections": list(BRIEF_SECTION_KEYS),
            "questions_asked": [],
            "messages": [{"role": "assistant", "content": reply}],
        }

    for key in BRIEF_SECTION_KEYS:
        if key not in sections_status:
            sections_status[key] = "empty" if key not in sections else "draft"

    incomplete = [k for k, v in sections_status.items() if v != "complete"]

    token_update = _accumulate_tokens(state, response)

    return {
        **token_update,
        "traces": [_build_trace_entry("generate", response)],
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


def update_and_respond(state: BriefGraphState) -> dict:  # noqa: PLR0915
    current_sections = state.get("document_sections", {})
    sections_status = state.get("sections_status", {})
    archetypes = state.get("archetypes", [])
    questions_asked = state.get("questions_asked", [])

    affected = state.get("sections_changed", [])
    if not affected:
        incomplete = [k for k, v in sections_status.items() if v != "complete"]
        affected = incomplete[:3] if incomplete else list(current_sections.keys())[:3]

    # Show ALL non-empty sections to the model so it always has the full
    # picture and never accidentally drops fields when patching one section.
    context_keys = [k for k in BRIEF_SECTION_KEYS if current_sections.get(k)]
    for key in affected:
        if key not in context_keys and current_sections.get(key):
            context_keys.append(key)

    sections_html_parts = []
    for key in context_keys:
        html = current_sections.get(key, "")
        if html:
            label = SECTION_LABELS.get(key, key)
            sections_html_parts.append(f"[{label}]\n{html}")

    methodology = _build_methodology_context(archetypes, affected)
    feedback = _build_feedback_context(affected)

    history_messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in state.get("messages", [])
    ]

    language_rule = _build_language_rule(state.get("document_language", ""))
    market_rule = _build_market_rule(state.get("document_language", ""))
    language_reminder = _build_language_reminder(state.get("document_language", ""))

    system_prompt = UPDATE_SYSTEM_PROMPT.format(
        methodology_context=methodology,
        feedback_context=feedback,
        language_rule=language_rule,
        market_rule=market_rule,
        current_sections_html="\n\n".join(sections_html_parts),
        sections_status_json=json.dumps(sections_status),
        questions_asked=", ".join(questions_asked) if questions_asked else "none yet",
    )

    messages = [{"role": "system", "content": system_prompt}, *history_messages]
    if language_reminder:
        messages.append({"role": "system", "content": language_reminder})

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
    section_patches = strip_wrong_language_patches(
        section_patches, state.get("document_language", "")
    )
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
    merged_sections = filter_scope_photo(merged_sections, archetypes)

    merged_status = dict(sections_status)
    merged_status.update(new_status)
    if "scope_photo" not in merged_sections:
        merged_status.pop("scope_photo", None)

    merged_structured = dict(state.get("structured_data", {}))
    merged_structured.update(structured_updates)

    changed_keys = list(section_patches.keys())
    new_questions_asked = list(questions_asked)
    new_questions_asked.extend(changed_keys)

    token_update = _accumulate_tokens(state, response)

    return {
        **token_update,
        "traces": [_build_trace_entry("update", response)],
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
    incomplete = [
        SECTION_LABELS.get(k, k) for k, v in sections_status.items() if v != "complete"
    ]
    language_rule = _build_language_rule(state.get("document_language", ""))
    market_rule = _build_market_rule(state.get("document_language", ""))
    language_reminder = _build_language_reminder(state.get("document_language", ""))

    messages = [
        {
            "role": "system",
            "content": ANSWER_SYSTEM_PROMPT.format(
                conversation_phase=state.get("conversation_phase", "questioning"),
                incomplete_sections=", ".join(incomplete) if incomplete else "none",
                language_rule=language_rule,
                market_rule=market_rule,
            ),
        },
    ]
    if language_reminder:
        messages.append({"role": "system", "content": language_reminder})
    messages.append({"role": "user", "content": user_message})

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
        "traces": [_build_trace_entry("answer", response)],
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
        "traces": [_build_trace_entry("extract", response)],
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
    document_language: str = "",
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
        "document_language": document_language,
        "traces": [],
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
        "traces": result.get("traces", []),
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
        "document_language": "",
        "traces": [],
    }

    result = extract_structured(state)
    return {
        "structured_data": result.get("structured_data", {}),
        "input_tokens": result.get("turn_input_tokens", 0),
        "output_tokens": result.get("turn_output_tokens", 0),
        "cost_usd": result.get("turn_cost_usd", 0.0),
        "model_used": result.get("model_used", ""),
        "traces": result.get("traces", []),
    }
