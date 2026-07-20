"""Unit tests for projects.stt — speech-to-text wrapper around Vertex Chirp 3."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from google.api_core import exceptions as google_exceptions
from google.cloud.speech_v2.types import cloud_speech

from aivus_backend.projects import stt
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefPrompt
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


@pytest.fixture
def client_profile(db) -> ClientModel:
    user = User.objects.create_user(
        email="stt-client@example.com",
        password="p@ssw0rd",
        name="STT Client",
        group="CLIENT",
    )
    return ClientModel.objects.create(name="Acme Corp", owner=user)


@pytest.fixture
def stt_terms_prompt(db) -> BriefPrompt:
    return BriefPrompt.objects.update_or_create(
        slug="stt_industry_terms",
        is_active=True,
        defaults={
            "title": "STT terms",
            "body": " ",
            "version": 1,
            "metadata": {
                "en": ["RFP", "treatment", "gaffer"],
                "ru": ["бриф", "тритмент"],
            },
        },
    )[0]


def _make_response(transcripts: list[str]) -> MagicMock:
    response = MagicMock(spec=cloud_speech.RecognizeResponse)
    response.results = [
        SimpleNamespace(alternatives=[SimpleNamespace(transcript=text)])
        for text in transcripts
    ]
    return response


def test_language_to_bcp47_known_codes():
    assert stt.language_to_bcp47("en") == "en-US"
    assert stt.language_to_bcp47("ru") == "ru-RU"
    assert stt.language_to_bcp47("EN") == "en-US"


def test_language_to_bcp47_falls_back_to_en_us_for_unknown():
    assert stt.language_to_bcp47("zz") == "en-US"
    assert stt.language_to_bcp47("") == "en-US"


def test_strip_mime_params_drops_codecs_segment():
    assert stt._strip_mime_params("audio/webm;codecs=opus") == "audio/webm"
    assert stt._strip_mime_params("AUDIO/MP4") == "audio/mp4"


def test_build_decoding_config_explicit_for_webm():
    explicit, auto = stt._build_decoding_config("audio/webm;codecs=opus")
    assert auto is None
    assert explicit is not None
    assert (
        explicit.encoding == cloud_speech.ExplicitDecodingConfig.AudioEncoding.WEBM_OPUS
    )
    assert explicit.sample_rate_hertz == 48000


def test_build_decoding_config_explicit_for_ogg():
    explicit, auto = stt._build_decoding_config("audio/ogg")
    assert auto is None
    assert explicit is not None
    assert (
        explicit.encoding == cloud_speech.ExplicitDecodingConfig.AudioEncoding.OGG_OPUS
    )


def test_build_decoding_config_auto_for_mp4():
    explicit, auto = stt._build_decoding_config("audio/mp4")
    assert explicit is None
    assert auto is not None


@pytest.mark.django_db
def test_build_phrase_hints_collects_dynamic_and_static(
    client_profile, stt_terms_prompt
):
    brief = Brief.objects.create(
        client=client_profile,
        title="Acme Corp Holiday Spot",
        structured_data={
            "clientName": "Acme Corp",
            "brand_name": "Acme",
            "projectName": "Holiday",
        },
    )
    dynamic, static = stt.build_phrase_hints(brief, "en")
    assert dynamic == ["Acme Corp Holiday Spot", "Acme Corp", "Acme", "Holiday"]
    assert "RFP" in static
    assert "treatment" in static


@pytest.mark.django_db
def test_build_phrase_hints_dedupes_case_insensitive(client_profile, stt_terms_prompt):
    brief = Brief.objects.create(
        client=client_profile,
        title="Acme",
        structured_data={"clientName": "ACME", "brand_name": "Acme"},
    )
    dynamic, _ = stt.build_phrase_hints(brief, "en")
    assert dynamic == ["Acme"]


@pytest.mark.django_db
def test_build_phrase_hints_uses_language_specific_terms(
    client_profile, stt_terms_prompt
):
    brief = Brief.objects.create(client=client_profile, title="x")
    _, static_ru = stt.build_phrase_hints(brief, "ru")
    assert "бриф" in static_ru
    assert "RFP" not in static_ru


@pytest.mark.django_db
def test_build_phrase_hints_truncates_to_max(
    client_profile, stt_terms_prompt, monkeypatch
):
    brief = Brief.objects.create(client=client_profile, title="x")
    monkeypatch.setattr(stt, "MAX_PHRASE_HINTS", 2)
    dynamic, static = stt.build_phrase_hints(brief, "en")
    assert len(dynamic) + len(static) <= 2


@pytest.mark.django_db
def test_build_phrase_hints_rejects_overlong_values(client_profile):
    brief = Brief.objects.create(
        client=client_profile,
        title="x" * 200,
    )
    dynamic, _ = stt.build_phrase_hints(brief, "en")
    assert dynamic == []


def test_build_adaptation_returns_none_for_empty():
    assert stt._build_adaptation([], []) is None


def test_build_adaptation_packs_phrases_with_separate_boost():
    adaptation = stt._build_adaptation(["Acme"], ["RFP"])
    assert adaptation is not None
    phrases = adaptation.phrase_sets[0].inline_phrase_set.phrases
    by_value = {p.value: p.boost for p in phrases}
    assert by_value["Acme"] == stt.DYNAMIC_BOOST
    assert by_value["RFP"] == stt.STATIC_BOOST


def test_classify_invalid_argument_duration():
    assert (
        stt._classify_invalid_argument("audio duration exceeds limit")
        == stt.ERROR_AUDIO_TOO_LONG
    )
    assert stt._classify_invalid_argument("longer than 60s") == stt.ERROR_AUDIO_TOO_LONG


def test_classify_invalid_argument_default_to_unsupported():
    assert (
        stt._classify_invalid_argument("invalid encoding")
        == stt.ERROR_UNSUPPORTED_FORMAT
    )


def test_transcribe_audio_rejects_oversized():
    with pytest.raises(stt.TranscriptionError) as exc_info:
        stt.transcribe_audio(b"x" * (stt.MAX_AUDIO_BYTES + 1), "audio/webm", "en")
    assert exc_info.value.code == stt.ERROR_AUDIO_TOO_LARGE


def test_transcribe_audio_rejects_unsupported_mime():
    with pytest.raises(stt.TranscriptionError) as exc_info:
        stt.transcribe_audio(b"abc", "video/mp4", "en")
    assert exc_info.value.code == stt.ERROR_UNSUPPORTED_FORMAT


def test_transcribe_audio_dev_fake_short_circuits(monkeypatch):
    monkeypatch.setenv("STT_DEV_FAKE", "1")
    result = stt.transcribe_audio(b"abc", "audio/webm", "en")
    assert "fake" in result["text"].lower()
    assert result["language"] == "en-US"
    assert result["model"] == stt.STT_MODEL


@patch.object(stt, "_get_speech_client")
def test_transcribe_audio_happy_path(mock_get_client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("STT_DEV_FAKE", raising=False)
    monkeypatch.setattr(stt, "STT_RECOGNIZER", "_")
    monkeypatch.setattr(stt, "STT_MODEL", "short")
    mock_client = MagicMock()
    mock_client.recognize.return_value = _make_response(["Hello world"])
    mock_get_client.return_value = mock_client

    result = stt.transcribe_audio(b"abc", "audio/webm", "en", ["Acme"], ["RFP"])

    assert result["text"] == "Hello world"
    assert result["language"] == "en-US"
    request = mock_client.recognize.call_args.kwargs["request"]
    assert "test-project" in request.recognizer
    assert request.recognizer.endswith("/recognizers/_")
    assert request.config.model == "short"
    assert request.config.language_codes == ["en-US"]
    phrases = request.config.adaptation.phrase_sets[0].inline_phrase_set.phrases
    assert {p.value for p in phrases} == {"Acme", "RFP"}


@patch.object(stt, "_get_speech_client")
def test_transcribe_audio_explicit_recognizer_chirp(mock_get_client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("STT_DEV_FAKE", raising=False)
    monkeypatch.setattr(stt, "STT_RECOGNIZER", "aivus-chirp3-auto")
    monkeypatch.setattr(stt, "STT_LANGUAGE_CODES", ["auto"])
    mock_client = MagicMock()
    mock_client.recognize.return_value = _make_response(["Привет мир"])
    mock_get_client.return_value = mock_client

    result = stt.transcribe_audio(b"abc", "audio/webm", "ru", ["Acme"], ["RFP"])

    assert result["text"] == "Привет мир"
    request = mock_client.recognize.call_args.kwargs["request"]
    assert request.recognizer.endswith("/recognizers/aivus-chirp3-auto")
    # model is baked into the explicit recognizer, not sent inline
    assert request.config.model == ""
    # chirp uses auto language detection from STT_LANGUAGE_CODES
    assert request.config.language_codes == ["auto"]
    # chirp rejects speech adaptation -> must not be attached
    assert not request.config.adaptation.phrase_sets


@patch.object(stt, "_get_speech_client")
def test_transcribe_audio_empty_result_raises_no_speech(mock_get_client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("STT_DEV_FAKE", raising=False)
    mock_client = MagicMock()
    mock_client.recognize.return_value = _make_response([])
    mock_get_client.return_value = mock_client
    with pytest.raises(stt.TranscriptionError) as exc_info:
        stt.transcribe_audio(b"abc", "audio/webm", "en")
    assert exc_info.value.code == stt.ERROR_NO_SPEECH


@patch.object(stt, "_get_speech_client")
def test_transcribe_audio_quota_exceeded(mock_get_client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("STT_DEV_FAKE", raising=False)
    mock_client = MagicMock()
    mock_client.recognize.side_effect = google_exceptions.ResourceExhausted("quota")
    mock_get_client.return_value = mock_client
    with pytest.raises(stt.TranscriptionError) as exc_info:
        stt.transcribe_audio(b"abc", "audio/webm", "en")
    assert exc_info.value.code == stt.ERROR_QUOTA_EXCEEDED


@patch.object(stt, "_get_speech_client")
def test_transcribe_audio_invalid_argument_duration_maps_too_long(
    mock_get_client, monkeypatch
):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("STT_DEV_FAKE", raising=False)
    mock_client = MagicMock()
    mock_client.recognize.side_effect = google_exceptions.InvalidArgument(
        "audio duration exceeds 60s"
    )
    mock_get_client.return_value = mock_client
    with pytest.raises(stt.TranscriptionError) as exc_info:
        stt.transcribe_audio(b"abc", "audio/webm", "en")
    assert exc_info.value.code == stt.ERROR_AUDIO_TOO_LONG


@patch.object(stt, "_get_speech_client")
def test_transcribe_audio_invalid_argument_other_maps_unsupported(
    mock_get_client, monkeypatch
):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("STT_DEV_FAKE", raising=False)
    mock_client = MagicMock()
    mock_client.recognize.side_effect = google_exceptions.InvalidArgument(
        "bad encoding"
    )
    mock_get_client.return_value = mock_client
    with pytest.raises(stt.TranscriptionError) as exc_info:
        stt.transcribe_audio(b"abc", "audio/webm", "en")
    assert exc_info.value.code == stt.ERROR_UNSUPPORTED_FORMAT
