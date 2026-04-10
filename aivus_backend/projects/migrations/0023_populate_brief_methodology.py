from django.db import migrations


def populate_methodology(apps, schema_editor):
    BriefMethodology = apps.get_model("projects", "BriefMethodology")

    entries = [
        {
            "archetype_code": None,
            "section_key": "",
            "title": "Common Questions (All Projects)",
            "content": (
                "These questions apply to EVERY project regardless of archetype. "
                "Ask them early in the conversation (first 3-5 exchanges):\n\n"
                "1. CLIENT & BRAND: Confirm client company name, brand name, "
                "and product/service being promoted.\n"
                "2. PROJECT TITLE: Suggest a working title based on the description.\n"
                "3. AGENCY: Is the client working through an agency? If yes, agency name.\n"
                "4. CONTACT: Client contact name, role, email, phone.\n"
                "5. BID DUE DATE: When do vendors need to submit their estimates? "
                "Typical: 2-3 business days.\n"
                "6. DEADLINE: When must the final deliverable be in hand? "
                "This determines rush surcharges.\n"
                "7. AWARD DATE: When will the client select the winning vendor?\n"
                "8. BUDGET STRATEGY: Use the threshold method rather than asking "
                "for an exact number. Ask: 'Which of these amounts feels too high "
                "for this project: $20k, $50k, $150k, $500k?' Then derive target "
                "budget from the answer. Alternatively offer ranges: "
                "'Is the budget closer to $20-50k, $50-150k, or $150k+?'\n"
                "9. VENDOR VISIBILITY: 'Do you want to reveal the budget target "
                "to vendors?' Recommend yes for better-tailored bids. Warn that "
                "without a guide, bids may vary wildly.\n"
                "10. TENDER PROCESS: What does the client expect from vendors?\n"
                "  - RFI: rough estimate + portfolio capability check (free)\n"
                "  - Bid & Treatment: fixed budget breakdown + director's "
                "creative vision (industry standard, usually free)\n"
                "  - Creative Pitch: vendors invent the idea/script. "
                "Ask if it is a paid pitch.\n"
                "  - Direct Award: vendor already chosen, just need the brief.\n"
                "11. NDA: Does the project require vendors to sign an NDA "
                "before receiving the brief? If yes, can the client attach "
                "the NDA document?\n\n"
                "After budget and scope are both known, provide a CALIBRATION: "
                "explain what is realistic at that budget level. If there is "
                "a mismatch (premium scope + tiny budget), explain the gap and "
                "ask: adjust budget, simplify scope, or proceed with a full "
                "brief anyway?"
            ),
            "priority": 0,
            "is_active": True,
        },
        {
            "archetype_code": None,
            "section_key": "deliverables",
            "title": "Final Deliverables Block (All Projects)",
            "content": (
                "After all scope questions are answered, compile the deliverables "
                "list. Use 'Suggest & Edit': generate a standard list based on "
                "archetype and usage, present it to the client for confirmation.\n\n"
                "HERO VIDEO: Confirm main video duration (:15, :30, :60, :90, "
                "2-3 min, custom).\n"
                "CUTDOWNS: Does the client need shorter edits? Common: :06 bumper, "
                ":15 pre-roll, :30 standard. Ask quantity of each.\n"
                "ASPECT RATIOS: Where will people watch?\n"
                "  - 16:9 Horizontal: TV, YouTube, desktop (standard)\n"
                "  - 9:16 Vertical: TikTok, Reels, Shorts (requires center-crop "
                "framing or separate edit)\n"
                "  - 4:5 Vertical: Facebook/Instagram feed\n"
                "  - 1:1 Square: LinkedIn, Facebook classic\n"
                "TECH SPECS:\n"
                "  - Web Ready (H.264/MP4): small file, ready to upload\n"
                "  - Broadcast Master (ProRes/DNxHD): TV stations require this\n"
                "  - Clean Feed (textless): for future text changes\n"
                "  - Subtitles (SRT or burned-in): which languages?\n"
                "SOURCE FILES: Does the client need raw footage or project files "
                "(Premiere/After Effects)? Note: vendors typically charge extra "
                "for these as intellectual property. Default: no.\n\n"
                "Present the final list as a summary table for client confirmation."
            ),
            "priority": 100,
            "is_active": True,
        },
        {
            "archetype_code": 1,
            "section_key": "strategic_foundation",
            "title": "Strategy Check (Creative Development)",
            "content": (
                "FIRST, check if the client already has a strategic brief or "
                "creative brief document describing audience, insights, and "
                "key message.\n"
                "- If YES: ask them to upload or describe it. Extract data and "
                "confirm: 'I see your audience is X and the key message is Y. "
                "Shall we move to deliverables?'\n"
                "- If NO: switch to 'Suggest & Edit' mode. Use the product/"
                "brand info to generate hypotheses the client can edit.\n\n"
                "AI-ASSISTED STRATEGY (Suggest & Edit mode):\n"
                "1. TARGET AUDIENCE: Generate a 2-3 sentence audience profile "
                "based on the product. Example: 'Male/Female, 18-34. Hardcore "
                "gamers who play 4+ hours daily. They value focus and reaction "
                "time over health benefits.' Ask client to confirm or edit.\n"
                "2. CONSUMER INSIGHT: Generate the core human truth or problem. "
                "Example: 'Gamers want to play longer to rank up, but physical "
                "fatigue makes them lose focus in critical moments.' Ask client "
                "to confirm or edit.\n"
                "3. SINGLE-MINDED PROPOSITION (SMP): Generate the one sentence "
                "the viewer should remember. Example: 'Volt Energy gives you "
                "Clean Focus to win without the crash.' Ask to confirm or edit.\n"
                "4. If the client says 'I don't know' for any of these, offer "
                "to add 'Strategy & Research' as a task for the vendor."
            ),
            "priority": 10,
            "is_active": True,
        },
        {
            "archetype_code": 1,
            "section_key": "deliverables",
            "title": "Creative Development Deliverables",
            "content": (
                "Since this is a development phase (not production), deliverables "
                "are documents, not video files. Ask which the client expects:\n"
                "- Creative Concept Deck: PDF with the 'Big Idea', visual "
                "references, and manifesto\n"
                "- Scripts: full text scripts for video (TVC/Digital)\n"
                "- Storyboards / Boardomatics: sketches of key frames\n"
                "- Moodboard / Visual Style: references for lighting, color, "
                "casting\n"
                "- Tagline / Copywriting: headlines and slogans\n"
                "- 360 Campaign Mechanics: ideas for Social, PR, and OOH "
                "integration\n\n"
                "Suggest the most common set based on the project type, then "
                "ask client to confirm or customize."
            ),
            "priority": 12,
            "is_active": True,
        },
        {
            "archetype_code": 1,
            "section_key": "",
            "title": "Pitch Process (Creative Development)",
            "content": (
                "Ask about the pitching terms. This is critical for creative "
                "projects:\n"
                "- PAID PITCH: Client has budget to pay selected vendors for "
                "their concepts (e.g., $1k-$5k per vendor). Attracts top-tier "
                "agencies.\n"
                "- FREE PITCH: Vendors pitch ideas for free hoping to win the "
                "production. Cuts out premium agencies but works for younger "
                "studios.\n"
                "- PORTFOLIO REVIEW: Client chooses a vendor based on past work, "
                "then hires them for a paid development project. Most fair "
                "approach.\n\n"
                "If the client wants a Creative Pitch, always ask: 'Is this a "
                "paid pitch? Top-tier agencies rarely pitch creative concepts "
                "for free.' Add the answer to budget_timeline section."
            ),
            "priority": 13,
            "is_active": True,
        },
        {
            "archetype_code": 2,
            "section_key": "creative_direction",
            "title": "Visual Style (High-End Production)",
            "content": (
                "Ask about the execution approach — this heavily impacts the "
                "production method:\n"
                "- Live Action: real people, locations, cameras\n"
                "- 3D / CGI Animation: high-end computer graphics (Pixar, "
                "tech ads style)\n"
                "- Mixed Media: live action + heavy VFX\n"
                "- 'Not sure — want the vendor to propose the best approach'\n\n"
                "REFERENCES: Ask for examples of visual quality or vibe. "
                "YouTube/Vimeo links are ideal. If no references, ask to "
                "describe the mood: cinematic, emotional, high-energy, "
                "minimalist, etc."
            ),
            "priority": 10,
            "is_active": True,
        },
        {
            "archetype_code": 2,
            "section_key": "scope_video",
            "title": "Talent & Casting (High-End Production)",
            "content": (
                "Talent fees can range from $500 to $500k. Clarify:\n"
                "- Professional Actors (Union/SAG-AFTRA): best performance, "
                "higher rates, strict rules on usage and overtime\n"
                "- Real People / Non-Union: authentic look, flexible rates\n"
                "- Celebrity / Influencer: requires separate negotiation, "
                "often the biggest line item\n"
                "- No on-screen talent: product shots only, voiceover\n"
                "- 'Not sure': suggest estimating for non-union as placeholder\n\n"
                "Also ask about Creative Readiness:\n"
                "- Script fully developed and approved\n"
                "- Rough concept/script that needs polish\n"
                "- No script — need the production company to develop "
                "creative concept and script from scratch (add Creative Fee "
                "to the brief)"
            ),
            "priority": 11,
            "is_active": True,
        },
        {
            "archetype_code": 2,
            "section_key": "usage_rights",
            "title": "Media Usage & Rights (High-End Production)",
            "content": (
                "Where will this video live? This dictates talent and music "
                "licensing costs:\n"
                "- Digital / Web Only: social media, YouTube, website. "
                "Worldwide perpetuity usually included.\n"
                "- Paid Media (Digital): targeted ads, pre-rolls, sponsored "
                "posts.\n"
                "- TV Broadcast: local or national? Which country/region?\n"
                "- Cinema: theatrical distribution.\n"
                "- OOH (Out of Home): digital screens, billboards.\n"
                "- 'Not sure': quote for 'Digital / Web Buyout' initially.\n\n"
                "Ask about territory (US only, North America, worldwide) "
                "and term duration (1 year, 2 years, perpetuity).\n"
                "If the project includes photos (archetype 5/6), ask about "
                "talent buyout for both video and stills in one question."
            ),
            "priority": 13,
            "is_active": True,
        },
        {
            "archetype_code": 3,
            "section_key": "strategic_foundation",
            "title": "Business Objective (Content/Social)",
            "content": (
                "Ask the business goal for the video. Use 'Suggest & Edit': "
                "generate a hypothesis based on the content type and ask "
                "client to confirm.\n\n"
                "AI suggestions by content type:\n"
                "- Event video: 'Create a highlight reel to show the scale "
                "of the event and sell tickets for next year.'\n"
                "- Explainer: 'Demonstrate how the product works to reduce "
                "customer support tickets.'\n"
                "- Social content: 'Increase brand awareness and engagement "
                "on Instagram/TikTok.'\n"
                "- Corporate film: 'Communicate company values and culture "
                "to attract talent.'\n"
                "- Training: 'Standardize onboarding process and reduce "
                "training time.'\n\n"
                "Present the suggestion and ask: 'Does this match your goal, "
                "or would you like to refine it?'"
            ),
            "priority": 10,
            "is_active": True,
        },
        {
            "archetype_code": 3,
            "section_key": "creative_direction",
            "title": "Visual Style & Budget Reality (Content/Social)",
            "content": (
                "For content projects, style should match budget. Offer "
                "budget-calibrated suggestions:\n"
                "- 'For $5-10k, we can do a quality screencast with motion "
                "design or a stock footage edit. Live action is possible but "
                "will use most of the budget.'\n"
                "- 'For $10-30k, professional interview setup with B-roll "
                "and simple motion graphics is standard.'\n"
                "- 'For $30-50k+, multi-camera shoot with styled locations "
                "and custom graphics.'\n\n"
                "Ask for reference videos. If none, offer style options: "
                "clean/corporate, dynamic/TikTok-style, documentary/authentic, "
                "animated/motion-graphics-only.\n\n"
                "SCRIPT: Ask if client has a script or shooting plan:\n"
                "- Script ready (vendor just executes)\n"
                "- Rough outline (bullet points)\n"
                "- No script (vendor writes — add copywriting to the brief)"
            ),
            "priority": 11,
            "is_active": True,
        },
        {
            "archetype_code": 3,
            "section_key": "scope_video",
            "title": "Logistics & Graphics (Content/Social)",
            "content": (
                "LOGISTICS (only if live action, skip for animation):\n"
                "- Location: client has a location (office, event hall) / "
                "vendor finds a studio / remote/stock only\n"
                "- Duration: half day (5h) / full day (10h) / multiple days / "
                "'not sure' (suggest based on scope)\n"
                "- Date: fixed date / flexible window (month/year)\n\n"
                "GRAPHICS & BRANDING:\n"
                "- Standard: lower thirds (names), logo intro/outro, subtitles\n"
                "- Advanced Motion Graphics: animated icons, charts, kinetic "
                "typography\n"
                "- Full Animation: entire video is animated, no real footage\n\n"
                "Suggest a standard package based on the project type and ask "
                "if the client needs more or less."
            ),
            "priority": 13,
            "is_active": True,
        },
        {
            "archetype_code": 4,
            "section_key": "post_production",
            "title": "Task Type (Post-Production)",
            "content": (
                "Classify the post-production request:\n"
                "- Creative Editing: storytelling from raw footage. Editor "
                "finds the story.\n"
                "- Technical / Online Editing: color correction, conform, "
                "mastering. Strict execution.\n"
                "- VFX / Cleanup: removing objects, beauty retouch, "
                "compositing, screen/sky replacement.\n"
                "- Motion Graphics: 2D/3D animation overlaid on video.\n"
                "- Localization: resizing, versioning, language adaptation.\n\n"
                "Multiple categories can apply. If the client says 'edit' — "
                "clarify if they need creative editing (find the story) or "
                "technical editing (follow an EDL/script)."
            ),
            "priority": 10,
            "is_active": True,
        },
        {
            "archetype_code": 4,
            "section_key": "post_production",
            "title": "Source Material & Creative Scope (Post-Production)",
            "content": (
                "SOURCE MATERIAL (critical for pricing):\n"
                "Format:\n"
                "- Professional RAW/Log (ARRI, RED, Sony Cinema): heavy files, "
                "maximum flexibility\n"
                "- High-res compressed (ProRes, DNxHD, high bitrate MP4): "
                "good for broadcast\n"
                "- Consumer/web (phone video, Zoom, stock): limited possibilities\n"
                "- Project files (Premiere XML, DaVinci project): finishing "
                "an existing edit\n"
                "- 'Not sure': ask about file sizes (GBs vs MBs)\n\n"
                "Volume:\n"
                "- Under 30 min of raw footage (small)\n"
                "- 1-5 hours (medium, e.g. event or interview)\n"
                "- 10+ hours (large, documentary/reality)\n"
                "- Single shot (for VFX/cleanup)\n\n"
                "SAMPLE: Can the client provide a sample clip or link to cloud "
                "storage? Vendors need to see what they are working with.\n\n"
                "CREATIVE SCOPE:\n"
                "- Script/EDL provided: strict execution, editor follows "
                "instructions (cheaper)\n"
                "- Creative freedom: editor watches everything, selects best "
                "moments, finds the story (more expensive, requires a "
                "creative editor)"
            ),
            "priority": 11,
            "is_active": True,
        },
        {
            "archetype_code": 4,
            "section_key": "post_production",
            "title": "VFX & Sound (Post-Production)",
            "content": (
                "VFX SPECIFICS (if applicable):\n"
                "- Simple removal: wire removal, logo blur, boom mic removal\n"
                "- Beauty work: skin smoothing, blemish removal\n"
                "- Compositing: screen replacement, sky replacement, "
                "adding 3D objects\n"
                "For VFX: ask if the camera is static (tripod) or moving — "
                "this dramatically affects complexity and cost. Ask about "
                "shot duration and number of VFX shots.\n\n"
                "LOCALIZATION (if applicable):\n"
                "- Language: voiceover dubbing or subtitles?\n"
                "- On-screen text: translating titles/graphics? "
                "(Need project files?)\n"
                "- Format: resizing from 16:9 to 9:16?\n\n"
                "SOUND:\n"
                "- Basic mix: balance volume, add background music (stock)\n"
                "- Sound design: add SFX (whooshes, footsteps, ambience)\n"
                "- Voiceover: record and mix a professional VO artist\n"
                "- Restoration: fix bad audio (remove wind/noise)"
            ),
            "priority": 14,
            "is_active": True,
        },
        {
            "archetype_code": 5,
            "section_key": "scope_photo",
            "title": "Subject & Style (Photography)",
            "content": (
                "Use 'Suggest & Edit': generate a photography approach based "
                "on the product/brand description:\n"
                "- Product Photography: high-end lighting, styled background, "
                "props. Focus on packaging and texture.\n"
                "- Lifestyle / Lookbook: models in real environments. "
                "Focus on mood and interaction.\n"
                "- Event Reportage: candid shots of speakers, guests, "
                "atmosphere.\n"
                "- Portrait / Headshots: professional portraits of team or "
                "talent.\n"
                "- E-commerce: white background, multiple angles, consistent "
                "lighting.\n\n"
                "Present the suggested approach and ask client to confirm "
                "or edit."
            ),
            "priority": 10,
            "is_active": True,
        },
        {
            "archetype_code": 5,
            "section_key": "scope_photo",
            "title": "Usage, Resolution & Quantity (Photography)",
            "content": (
                "USAGE & RESOLUTION (determines camera and retouching):\n"
                "- Social Media / Web Only: standard resolution is fine\n"
                "- Print (magazines, in-store): high resolution required\n"
                "- OOH (billboards): ultra-high resolution, may require "
                "medium format camera (Phase One, Hasselblad 100MP+). "
                "Note this in the brief.\n"
                "- Packaging: specific die-cut requirements\n\n"
                "QUANTITY:\n"
                "- Small pack: 5-10 hero images (common for ad campaigns)\n"
                "- Lookbook / social pack: 20-50 images (fashion, monthly "
                "content)\n"
                "- Bulk / e-commerce: 100+ images (catalog style)\n"
                "- Event coverage: 200+ shots with light color correction\n\n"
                "LOGISTICS:\n"
                "- Studio: vendor provides or rents\n"
                "- Location: client has a specific place\n"
                "- Remote: product shipped to photographer\n"
                "- On set: shoot simultaneously with video (important for "
                "scheduling)"
            ),
            "priority": 11,
            "is_active": True,
        },
        {
            "archetype_code": 5,
            "section_key": "scope_photo",
            "title": "Design / Key Visual Scope (Photography)",
            "content": (
                "Ask if the client needs finished ads (Key Visuals) or just "
                "clean retouched photos:\n"
                "- Photos only: deliver clean, retouched high-res JPEGs/TIFFs\n"
                "- Key Visual Design: composition, typography, logos, and "
                "graphic elements added. This is the transition from "
                "photography to design (archetype 6).\n\n"
                "If KV Design is needed:\n"
                "- How many unique KV concepts? (e.g., 1 main KV adapted "
                "to 3 formats)\n"
                "- What formats? List sizes for distribution: banners, "
                "stories, billboards 3x6, etc.\n"
                "- Source: KV from photos, from video stills, or drawn "
                "from scratch (CGI/collage)?"
            ),
            "priority": 12,
            "is_active": True,
        },
        {
            "archetype_code": 6,
            "section_key": "scope_photo",
            "title": "Subject & Style (Key Visual / Design)",
            "content": (
                "Use 'Suggest & Edit': generate a photography approach based "
                "on the product/brand description:\n"
                "- Product Photography: high-end lighting, styled background, "
                "props. Focus on packaging and texture.\n"
                "- Lifestyle / Lookbook: models in real environments. "
                "Focus on mood and interaction.\n"
                "- Event Reportage: candid shots of speakers, guests, "
                "atmosphere.\n"
                "- Portrait / Headshots: professional portraits of team or "
                "talent.\n"
                "- E-commerce: white background, multiple angles, consistent "
                "lighting.\n\n"
                "Present the suggested approach and ask client to confirm "
                "or edit."
            ),
            "priority": 10,
            "is_active": True,
        },
        {
            "archetype_code": 6,
            "section_key": "scope_photo",
            "title": "Usage, Resolution & Quantity (Key Visual / Design)",
            "content": (
                "USAGE & RESOLUTION (determines camera and retouching):\n"
                "- Social Media / Web Only: standard resolution is fine\n"
                "- Print (magazines, in-store): high resolution required\n"
                "- OOH (billboards): ultra-high resolution, may require "
                "medium format camera (Phase One, Hasselblad 100MP+). "
                "Note this in the brief.\n"
                "- Packaging: specific die-cut requirements\n\n"
                "QUANTITY:\n"
                "- Small pack: 5-10 hero images (common for ad campaigns)\n"
                "- Lookbook / social pack: 20-50 images (fashion, monthly "
                "content)\n"
                "- Bulk / e-commerce: 100+ images (catalog style)\n"
                "- Event coverage: 200+ shots with light color correction\n\n"
                "LOGISTICS:\n"
                "- Studio: vendor provides or rents\n"
                "- Location: client has a specific place\n"
                "- Remote: product shipped to photographer\n"
                "- On set: shoot simultaneously with video (important for "
                "scheduling)"
            ),
            "priority": 11,
            "is_active": True,
        },
        {
            "archetype_code": 6,
            "section_key": "scope_photo",
            "title": "Design / Key Visual Scope (Key Visual / Design)",
            "content": (
                "Ask if the client needs finished ads (Key Visuals) or just "
                "clean retouched photos:\n"
                "- Photos only: deliver clean, retouched high-res JPEGs/TIFFs\n"
                "- Key Visual Design: composition, typography, logos, and "
                "graphic elements added.\n\n"
                "If KV Design is needed:\n"
                "- How many unique KV concepts? (e.g., 1 main KV adapted "
                "to 3 formats)\n"
                "- What formats? List sizes for distribution: banners, "
                "stories, billboards 3x6, etc.\n"
                "- Source: KV from photos, from video stills, or drawn "
                "from scratch (CGI/collage)?"
            ),
            "priority": 12,
            "is_active": True,
        },
    ]

    BriefMethodology.objects.bulk_create(
        [BriefMethodology(**entry) for entry in entries]
    )


def remove_methodology(apps, schema_editor):
    BriefMethodology = apps.get_model("projects", "BriefMethodology")
    BriefMethodology.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0022_brief_questions_asked"),
    ]

    operations = [
        migrations.RunPython(populate_methodology, remove_methodology),
    ]
