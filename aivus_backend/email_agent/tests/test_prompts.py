"""The email-agent prompt slugs are seeded and active."""

import pytest

from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.projects.models import BriefPrompt

pytestmark = pytest.mark.django_db

EMAIL_SLUGS = [
    BriefPromptSlug.EMAIL_CLASSIFICATION,
    BriefPromptSlug.EMAIL_REPLY,
    BriefPromptSlug.EMAIL_FOLLOWUP,
    BriefPromptSlug.AGENT_ONBOARDING,
]


@pytest.mark.parametrize("slug", EMAIL_SLUGS)
def test_email_prompt_is_seeded_and_active(slug):
    body = BriefPrompt.get_active_body(slug)
    assert body
    assert BriefPrompt.objects.filter(slug=slug, is_active=True).count() == 1
