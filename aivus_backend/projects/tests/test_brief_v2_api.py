import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient

from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFeedback
from aivus_backend.projects.models import ChatMessage
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def client_user(db):
    return User.objects.create_user(
        email="brief-v2-client@example.com",
        password="testpass123",
        name="Brief V2 Client",
        group="CLIENT",
    )


@pytest.fixture
def client_profile(client_user):
    return ClientModel.objects.create(name="Test Client Co", owner=client_user)


def _client_headers(user, client_id=None):
    headers = {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": "CLIENT",
    }
    if client_id:
        headers["HTTP_X_CLIENT_ID"] = str(client_id)
    return headers


class TestClientBriefAiStart:
    def test_requires_auth(self, api_client):
        response = api_client.post(
            "/api/v1/client/briefs/ai/start",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("aivus_backend.projects.tasks.generate_brief_task.delay")
    def test_creates_brief_and_returns_task_id(
        self, mock_delay, api_client, client_user, client_profile
    ):
        mock_delay.return_value.id = "fake-task-id"

        response = api_client.post(
            "/api/v1/client/briefs/ai/start",
            data=json.dumps({"message": "30-second TVC for Nike"}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 201
        data = response.json()
        assert "briefId" in data
        assert data["taskId"] == "fake-task-id"

        brief = Brief.objects.get(id=data["briefId"])
        assert brief.client == client_profile
        assert brief.status == "DRAFT"

        assert ChatMessage.objects.filter(brief=brief, role="user").exists()

    def test_empty_message_rejected(self, api_client, client_user, client_profile):
        response = api_client.post(
            "/api/v1/client/briefs/ai/start",
            data=json.dumps({"message": "   "}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_invalid_json_rejected(self, api_client, client_user, client_profile):
        response = api_client.post(
            "/api/v1/client/briefs/ai/start",
            data="not json",
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400


class TestClientBriefAiChat:
    @pytest.fixture
    def brief(self, client_profile):
        return Brief.objects.create(client=client_profile, status="DRAFT")

    @patch("aivus_backend.projects.ai_brief_v2.process_brief_message")
    def test_sends_message_and_returns_response(
        self, mock_process, api_client, client_user, client_profile, brief
    ):
        mock_process.return_value = {
            "reply": "Great! Let me help.",
            "document_sections": {},
            "sections_status": {},
            "archetypes": [],
            "structured_data": {},
            "conversation_phase": "questioning",
            "sections_changed": ["project_header"],
            "section_patches": {"project_header": "<h2>Header</h2>"},
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.001,
            "model_used": "gpt-4o-mini",
        }

        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "Budget is $500K"}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "Great! Let me help."
        assert "project_header" in data["sectionsChanged"]
        assert data["conversationPhase"] == "questioning"

    def test_not_found_for_wrong_client(
        self, api_client, client_user, client_profile, brief, db
    ):
        other_user = User.objects.create_user(
            email="other-client@example.com",
            password="testpass123",
            name="Other Client",
            group="CLIENT",
        )
        other_client = ClientModel.objects.create(name="Other Co", owner=other_user)

        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            **_client_headers(other_user, other_client.id),
        )
        assert response.status_code == 404


class TestClientBriefAiSection:
    @pytest.fixture
    def brief(self, client_profile):
        return Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            document_sections={"project_header": "<h2>Old</h2>"},
            version=1,
        )

    def test_updates_section(self, api_client, client_user, client_profile, brief):
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "html": "<h2>Updated Header</h2>",
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 2

        brief.refresh_from_db()
        assert "Updated Header" in brief.document_sections["project_header"]

    def test_version_conflict(self, api_client, client_user, client_profile, brief):
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "html": "<h2>Conflict</h2>",
                    "expectedVersion": 999,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 409


class TestClientBriefAiFeedback:
    @pytest.fixture
    def brief(self, client_profile):
        return Brief.objects.create(client=client_profile, status="DRAFT")

    def test_creates_feedback(self, api_client, client_user, client_profile, brief):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/feedback",
            data=json.dumps(
                {
                    "sectionKey": "scope_video",
                    "rating": "down",
                    "comment": "Too vague",
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["rating"] == "down"
        assert BriefFeedback.objects.filter(brief=brief).count() == 1


class TestPublicBriefAiStart:
    @patch("aivus_backend.projects.tasks.generate_brief_task.delay")
    def test_creates_anonymous_brief(self, mock_delay, api_client, db):
        mock_delay.return_value.id = "fake-public-task-id"

        response = api_client.post(
            "/api/v1/public/briefs/ai/start",
            data=json.dumps({"message": "Music video for indie band"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert "briefId" in data
        assert "token" in data
        assert data["taskId"] == "fake-public-task-id"

        brief = Brief.objects.get(id=data["briefId"])
        assert brief.client is None
        assert brief.anonymous_token == data["token"]


class TestPublicBriefAiChat:
    @pytest.fixture
    def anonymous_brief(self, db):
        return Brief.objects.create(
            client=None,
            status="DRAFT",
            anonymous_token="test-anon-token-12345",
        )

    @patch("aivus_backend.projects.ai_brief_v2.process_brief_message")
    def test_anonymous_chat(self, mock_process, api_client, anonymous_brief):
        mock_process.return_value = {
            "reply": "Sure, tell me about the budget.",
            "document_sections": {},
            "sections_status": {},
            "archetypes": [],
            "structured_data": {},
            "conversation_phase": "questioning",
            "sections_changed": [],
            "section_patches": {},
            "input_tokens": 80,
            "output_tokens": 40,
            "cost_usd": 0.0005,
            "model_used": "gpt-4o-mini",
        }

        response = api_client.post(
            f"/api/v1/public/briefs/ai/{anonymous_brief.id}/chat",
            data=json.dumps({"message": "Budget is $10K"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="test-anon-token-12345",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "Sure, tell me about the budget."

    def test_wrong_token_rejected(self, api_client, anonymous_brief):
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{anonymous_brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="wrong-token",
        )
        assert response.status_code == 404

    def test_no_token_rejected(self, api_client, anonymous_brief):
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{anonymous_brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 404


class TestPublicBriefClaim:
    @pytest.fixture
    def anonymous_brief(self, db):
        return Brief.objects.create(
            client=None,
            status="DRAFT",
            anonymous_token="claim-test-token",
        )

    def test_claim_attaches_to_client(
        self, api_client, client_user, client_profile, anonymous_brief
    ):
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{anonymous_brief.id}/claim",
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="claim-test-token",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200

        anonymous_brief.refresh_from_db()
        assert anonymous_brief.client == client_profile
        assert anonymous_brief.anonymous_token is None
        assert anonymous_brief.claimed_at is not None

    def test_claim_wrong_token_fails(
        self, api_client, client_user, client_profile, anonymous_brief
    ):
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{anonymous_brief.id}/claim",
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="wrong-token",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 404


class TestMessageLimit:
    def test_auth_message_limit(self, api_client, client_user, client_profile, db):
        brief = Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            message_count=50,
        )
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 429

    def test_anon_message_limit(self, api_client, db):
        brief = Brief.objects.create(
            client=None,
            status="DRAFT",
            anonymous_token="limit-token",
            message_count=20,
        )
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="limit-token",
        )
        assert response.status_code == 429


class TestSanitization:
    def test_section_html_sanitized(self, api_client, client_user, client_profile, db):
        brief = Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            document_sections={"project_header": "<p>Old</p>"},
            version=1,
        )
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "html": '<p>Safe</p><script>alert("xss")</script>',
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200

        brief.refresh_from_db()
        assert "<script>" not in brief.document_sections["project_header"]
        assert "Safe" in brief.document_sections["project_header"]


class TestFinalizedBriefRejection:
    @pytest.fixture
    def completed_brief(self, client_profile):
        return Brief.objects.create(
            client=client_profile,
            status="COMPLETED",
            document_sections={"project_header": "<h2>Done</h2>"},
            version=5,
        )

    def test_chat_rejected_on_completed_brief(
        self, api_client, client_user, client_profile, completed_brief
    ):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{completed_brief.id}/chat",
            data=json.dumps({"message": "change the budget"}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 409

    def test_section_edit_rejected_on_completed_brief(
        self, api_client, client_user, client_profile, completed_brief
    ):
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{completed_brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "html": "<h2>New</h2>",
                    "expectedVersion": 5,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 409

    def test_finalize_rejected_on_completed_brief(
        self, api_client, client_user, client_profile, completed_brief
    ):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{completed_brief.id}/finalize",
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 409

    def test_public_chat_rejected_on_completed_brief(self, api_client, db):
        brief = Brief.objects.create(
            client=None,
            status="COMPLETED",
            anonymous_token="completed-token",
        )
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="completed-token",
        )
        assert response.status_code == 409


class TestInvalidSectionKey:
    @pytest.fixture
    def brief(self, client_profile):
        return Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            document_sections={"project_header": "<h2>Ok</h2>"},
            version=1,
        )

    def test_unknown_section_key_rejected(
        self, api_client, client_user, client_profile, brief
    ):
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "nonexistent_section",
                    "html": "<p>hack</p>",
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_empty_section_key_rejected(
        self, api_client, client_user, client_profile, brief
    ):
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "",
                    "html": "<p>hack</p>",
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400


class TestInvalidFeedbackRating:
    @pytest.fixture
    def brief(self, client_profile):
        return Brief.objects.create(client=client_profile, status="DRAFT")

    def test_invalid_rating_rejected(
        self, api_client, client_user, client_profile, brief
    ):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/feedback",
            data=json.dumps(
                {
                    "sectionKey": "scope_video",
                    "rating": "invalid_value",
                    "comment": "test",
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_comment_too_long_rejected(
        self, api_client, client_user, client_profile, brief
    ):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/feedback",
            data=json.dumps(
                {
                    "sectionKey": "scope_video",
                    "rating": "down",
                    "comment": "x" * 2001,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400


class TestMessageLengthLimits:
    def test_auth_message_too_long(self, api_client, client_user, client_profile, db):
        brief = Brief.objects.create(client=client_profile, status="DRAFT")
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "a" * 10001}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_anon_message_too_long(self, api_client, db):
        brief = Brief.objects.create(
            client=None,
            status="DRAFT",
            anonymous_token="length-token",
        )
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "a" * 10001}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="length-token",
        )
        assert response.status_code == 400

    def test_section_html_too_long(self, api_client, client_user, client_profile, db):
        brief = Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            document_sections={"project_header": "<p>Ok</p>"},
            version=1,
        )
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "html": "<p>" + "x" * 50001 + "</p>",
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    @patch("aivus_backend.projects.tasks.generate_brief_task.delay")
    def test_start_message_too_long(
        self, mock_delay, api_client, client_user, client_profile
    ):
        response = api_client.post(
            "/api/v1/client/briefs/ai/start",
            data=json.dumps({"message": "b" * 10001}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400
        mock_delay.assert_not_called()


class TestClaimTokenCleanup:
    def test_claim_clears_chat_message_tokens(
        self, api_client, client_user, client_profile, db
    ):
        token = "cleanup-token-12345"
        brief = Brief.objects.create(
            client=None,
            status="DRAFT",
            anonymous_token=token,
        )
        ChatMessage.objects.create(
            brief=brief, user=None, anonymous_token=token, role="user", content="test"
        )
        ChatMessage.objects.create(
            brief=brief,
            user=None,
            anonymous_token=token,
            role="assistant",
            content="reply",
        )

        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/claim",
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN=token,
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200

        for msg in ChatMessage.objects.filter(brief=brief):
            assert msg.anonymous_token == ""

    def test_claim_already_claimed_fails(
        self, api_client, client_user, client_profile, db
    ):
        brief = Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            anonymous_token=None,
        )
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/claim",
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="some-token",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 404


class TestLLMFailureHandling:
    @patch("aivus_backend.projects.ai_brief_v2.process_brief_message")
    def test_auth_chat_returns_500_on_llm_failure(
        self, mock_process, api_client, client_user, client_profile, db
    ):
        mock_process.side_effect = RuntimeError("LLM timeout")
        brief = Brief.objects.create(client=client_profile, status="DRAFT")

        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 500

    @patch("aivus_backend.projects.ai_brief_v2.process_brief_message")
    def test_public_chat_returns_500_on_llm_failure(self, mock_process, api_client, db):
        mock_process.side_effect = RuntimeError("LLM timeout")
        brief = Brief.objects.create(
            client=None, status="DRAFT", anonymous_token="llm-fail-token"
        )

        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="llm-fail-token",
        )
        assert response.status_code == 500


class TestEdgeCases:
    def test_missing_brief_token_header(self, api_client, db):
        brief = Brief.objects.create(
            client=None, status="DRAFT", anonymous_token="edge-token"
        )
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_claim_without_token_header(
        self, api_client, client_user, client_profile, db
    ):
        brief = Brief.objects.create(
            client=None, status="DRAFT", anonymous_token="no-header-token"
        )
        response = api_client.post(
            f"/api/v1/public/briefs/ai/{brief.id}/claim",
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_chat_invalid_json_body(self, api_client, client_user, client_profile, db):
        brief = Brief.objects.create(client=client_profile, status="DRAFT")
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data="not-json{{",
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_section_edit_missing_html(
        self, api_client, client_user, client_profile, db
    ):
        brief = Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            document_sections={"project_header": "<p>Test</p>"},
            version=1,
        )
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200

    def test_feedback_without_message_id(
        self, api_client, client_user, client_profile, db
    ):
        brief = Brief.objects.create(client=client_profile, status="DRAFT")
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/feedback",
            data=json.dumps(
                {
                    "sectionKey": "budget_timeline",
                    "rating": "up",
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["rating"] == "up"


class TestFeedbackSectionKeyValidation:
    @pytest.fixture
    def brief(self, client_profile):
        return Brief.objects.create(client=client_profile, status="DRAFT")

    def test_invalid_section_key_in_feedback_rejected(
        self, api_client, client_user, client_profile, brief
    ):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/feedback",
            data=json.dumps(
                {
                    "sectionKey": "hacked_section",
                    "rating": "down",
                    "comment": "test",
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 400

    def test_empty_section_key_in_feedback_allowed(
        self, api_client, client_user, client_profile, brief
    ):
        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/feedback",
            data=json.dumps(
                {
                    "sectionKey": "",
                    "rating": "up",
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 201


class TestPublicStartValidation:
    def test_public_start_empty_message(self, api_client, db):
        response = api_client.post(
            "/api/v1/public/briefs/ai/start",
            data=json.dumps({"message": "  "}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_public_start_message_too_long(self, api_client, db):
        response = api_client.post(
            "/api/v1/public/briefs/ai/start",
            data=json.dumps({"message": "x" * 10001}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_public_start_no_message_field(self, api_client, db):
        response = api_client.post(
            "/api/v1/public/briefs/ai/start",
            data=json.dumps({"text": "hello"}),
            content_type="application/json",
        )
        assert response.status_code == 400


class TestFinalizeSuccess:
    @patch("aivus_backend.projects.tasks.finalize_brief_task.delay")
    def test_finalize_returns_task_id(
        self, mock_delay, api_client, client_user, client_profile, db
    ):
        mock_delay.return_value.id = "finalize-task-id"
        brief = Brief.objects.create(client=client_profile, status="DRAFT")

        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/finalize",
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["taskId"] == "finalize-task-id"
        mock_delay.assert_called_once_with(str(brief.id))


class TestChatMessageSideEffects:
    @patch("aivus_backend.projects.ai_brief_v2.process_brief_message")
    def test_chat_creates_both_messages(
        self, mock_process, api_client, client_user, client_profile, db
    ):
        mock_process.return_value = {
            "reply": "Got it.",
            "document_sections": {},
            "sections_status": {},
            "archetypes": [],
            "structured_data": {},
            "conversation_phase": "questioning",
            "sections_changed": [],
            "section_patches": {},
            "input_tokens": 50,
            "output_tokens": 30,
            "cost_usd": 0.0005,
            "model_used": "gpt-4o-mini",
        }

        brief = Brief.objects.create(client=client_profile, status="DRAFT")

        response = api_client.post(
            f"/api/v1/client/briefs/ai/{brief.id}/chat",
            data=json.dumps({"message": "test message"}),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200

        user_msgs = ChatMessage.objects.filter(brief=brief, role="user")
        assistant_msgs = ChatMessage.objects.filter(brief=brief, role="assistant")
        assert user_msgs.count() == 1
        assert assistant_msgs.count() == 1
        first_user_msg = user_msgs.first()
        first_assistant_msg = assistant_msgs.first()
        assert first_user_msg is not None
        assert first_assistant_msg is not None
        assert first_user_msg.content == "test message"
        assert first_assistant_msg.content == "Got it."


class TestSanitizationLinkRel:
    def test_links_get_noopener(self, api_client, client_user, client_profile, db):
        brief = Brief.objects.create(
            client=client_profile,
            status="DRAFT",
            document_sections={"project_header": "<p>Old</p>"},
            version=1,
        )
        response = api_client.patch(
            f"/api/v1/client/briefs/ai/{brief.id}/section",
            data=json.dumps(
                {
                    "sectionKey": "project_header",
                    "html": '<p><a href="https://evil.com">click</a></p>',
                    "expectedVersion": 1,
                }
            ),
            content_type="application/json",
            **_client_headers(client_user, client_profile.id),
        )
        assert response.status_code == 200

        brief.refresh_from_db()
        saved = brief.document_sections["project_header"]
        assert 'rel="noopener noreferrer"' in saved
