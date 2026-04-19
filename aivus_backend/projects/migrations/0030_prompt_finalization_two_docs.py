"""Roll finalization_prompt to v2: deliverables folded into production brief (no separate checklist)."""

from django.db import migrations

DEFAULT_MODEL = "gemini-3.1-pro-preview"

FINALIZATION_V2_BODY = """\
You are the same producer who just ran the whole brief-creation conversation with
this client. Now it is time to hand them the final package.

Look at the entire conversation above. Do NOT ask the client any more questions —
produce the final deliverables based on what was discussed. If some minor fields
were never explicitly covered, fill them with reasonable industry defaults for the
detected market/language (US vs RF) and do not mark them as TBD unless they truly
can't be inferred.

Produce two documents, both in the same language that the conversation is in and
aligned with the client's market:

1. Production Brief — a complete, professional, vendor-ready brief based on MASTER
   BRIEF TEMPLATE. Include only the sections that actually apply to this project
   (skip sections irrelevant to the archetype). No placeholders, no TBDs the
   client doesn't explicitly want, no internal notes, no instructions. Ready to
   paste into Word.

   IMPORTANT: the brief MUST include a dedicated section "Deliverables" (named
   appropriately in the target language — e.g. "Deliverables" / "Состав
   поставки"). That section is a clean bulleted list of every asset the client
   should receive at the end of the project: hero videos with durations, cutdowns
   with aspect ratios and durations, stills/KVs with sizes, file formats, source
   files policy, etc. Industry-accurate. It is part of the main brief, not a
   separate document.

2. Vendor Outreach Email — a short, friendly, professional email the client can
   send to production vendors to invite them to the tender. Include a clear subject
   line (as a <h1> inside the HTML, and on a separate line at the top of the plain
   text version). Reference the attached/linked brief.

OUTPUT FORMAT (STRICT):
Reply with valid JSON and nothing else. No markdown, no comments.

{
  "production_brief_html": "<well-formed HTML with h2/h3/ul/li/strong>",
  "vendor_email_html": "<HTML with <h1>Subject</h1> then paragraphs>",
  "vendor_email_text": "Subject: <subject>\\n\\n<plain-text body>"
}

All HTML must be clean and ready to paste into Word — semantic tags only
(h1, h2, h3, p, ul, ol, li, strong, em, a, hr, table/tr/td). No inline styles,
no scripts, no markdown fences.
"""


def add_v2(apps, schema_editor):
    BriefPrompt = apps.get_model("projects", "BriefPrompt")
    active = (
        BriefPrompt.objects.filter(slug="finalization_prompt", is_active=True)
        .order_by("-version")
        .first()
    )
    last = (
        BriefPrompt.objects.filter(slug="finalization_prompt")
        .order_by("-version")
        .first()
    )
    next_version = (last.version + 1) if last else 2
    model_name = active.model_name if active else DEFAULT_MODEL
    metadata = active.metadata if active else {}

    BriefPrompt.objects.filter(slug="finalization_prompt", is_active=True).update(
        is_active=False
    )
    BriefPrompt.objects.create(
        slug="finalization_prompt",
        title=f"Finalization prompt (v{next_version})",
        body=FINALIZATION_V2_BODY,
        version=next_version,
        is_active=True,
        model_name=model_name,
        metadata=metadata,
    )


def revert_v2(apps, schema_editor):
    BriefPrompt = apps.get_model("projects", "BriefPrompt")
    latest = (
        BriefPrompt.objects.filter(slug="finalization_prompt")
        .order_by("-version")
        .first()
    )
    if not latest or latest.version < 2:
        return
    latest.delete()
    prev = (
        BriefPrompt.objects.filter(slug="finalization_prompt")
        .order_by("-version")
        .first()
    )
    if prev:
        BriefPrompt.objects.filter(
            slug="finalization_prompt", is_active=True
        ).update(is_active=False)
        prev.is_active = True
        prev.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0029_prompt_no_brief_dump"),
    ]

    operations = [
        migrations.RunPython(add_v2, revert_v2),
    ]
