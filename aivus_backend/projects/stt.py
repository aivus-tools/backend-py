"""Speech-to-Text via Google Cloud Speech v2 (Chirp 3)."""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

from google.api_core import exceptions as google_exceptions
from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech
from google.oauth2 import service_account

from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.projects.models import BriefPrompt

if TYPE_CHECKING:
    from aivus_backend.projects.models import Brief

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_DURATION_SEC = 60
STT_MODEL = os.environ.get("STT_MODEL", "short")
STT_RECOGNIZER = os.environ.get("STT_RECOGNIZER", "_")

ALLOWED_AUDIO_MIMES = frozenset(
    {
        "audio/webm",
        "audio/webm;codecs=opus",
        "audio/ogg",
        "audio/ogg;codecs=opus",
        "audio/mp4",
        "audio/mp4a-latm",
        "audio/mpeg",
        "audio/aac",
        "audio/x-m4a",
    },
)

AUDIO_SNIFF_VIDEO_ALIASES = frozenset(
    {
        "video/webm",
        "video/ogg",
        "video/mp4",
        "video/x-m4a",
    },
)

MAX_PHRASE_HINTS = 1000
MAX_PHRASE_LENGTH = 100
DYNAMIC_BOOST = 8.0
STATIC_BOOST = 3.0

LANGUAGE_BCP47 = {
    "en": "en-US",
    "ru": "ru-RU",
}

# Languages for an explicit recognizer (e.g. chirp_3). "auto" lets chirp_3
# detect the spoken language from 100+ supported languages; alternatively set a
# small BCP-47 list (e.g. "en-US,ru-RU") to constrain detection. Ignored for the
# synthetic "_" recognizer, which always uses the single language from the
# request.
STT_LANGUAGE_CODES = [
    x.strip()
    for x in os.environ.get("STT_LANGUAGE_CODES", "auto").split(",")
    if x.strip()
] or ["auto"]

ERROR_UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
ERROR_AUDIO_TOO_LARGE = "AUDIO_TOO_LARGE"
ERROR_AUDIO_TOO_LONG = "AUDIO_TOO_LONG"
ERROR_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
ERROR_NO_SPEECH = "NO_SPEECH_DETECTED"
ERROR_INTERNAL = "INTERNAL"

_DURATION_KEYWORDS = ("duration", "too long", "exceeds", "longer than")


class TranscriptionError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _resolve_location() -> str:
    return os.environ.get("GOOGLE_CLOUD_SPEECH_LOCATION", "global")


def _resolve_endpoint(location: str) -> str | None:
    if location == "global":
        return None
    return f"{location}-speech.googleapis.com"


_speech_client_lock = threading.Lock()
_speech_client: SpeechClient | None = None


def _get_speech_client() -> SpeechClient:
    global _speech_client  # noqa: PLW0603
    if _speech_client is not None:
        return _speech_client
    with _speech_client_lock:
        if _speech_client is not None:
            return _speech_client
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not project:
            msg = "GOOGLE_CLOUD_PROJECT is not set"
            raise ValueError(msg)
        location = _resolve_location()
        endpoint = _resolve_endpoint(location)
        client_options = ClientOptions(api_endpoint=endpoint) if endpoint else None
        credentials = None
        credentials_path = os.environ.get("VERTEX_CREDENTIALS_PATH", "")
        if credentials_path:
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        _speech_client = SpeechClient(
            client_options=client_options,
            credentials=credentials,
        )
        return _speech_client


def reset_speech_client() -> None:
    global _speech_client  # noqa: PLW0603
    with _speech_client_lock:
        _speech_client = None


def language_to_bcp47(code: str) -> str:
    return LANGUAGE_BCP47.get((code or "en").lower(), "en-US")


def _strip_mime_params(mime: str) -> str:
    return (mime or "").split(";")[0].strip().lower()


def _is_explicit_webm_or_ogg(mime: str) -> bool:
    return _strip_mime_params(mime) in {"audio/webm", "audio/ogg"}


_ExplicitOrAuto = tuple[
    cloud_speech.ExplicitDecodingConfig | None,
    cloud_speech.AutoDetectDecodingConfig | None,
]


def _build_decoding_config(mime: str) -> _ExplicitOrAuto:
    if not _is_explicit_webm_or_ogg(mime):
        return None, cloud_speech.AutoDetectDecodingConfig()
    base = _strip_mime_params(mime)
    encoding = (
        cloud_speech.ExplicitDecodingConfig.AudioEncoding.WEBM_OPUS
        if base == "audio/webm"
        else cloud_speech.ExplicitDecodingConfig.AudioEncoding.OGG_OPUS
    )
    explicit = cloud_speech.ExplicitDecodingConfig(
        encoding=encoding,
        sample_rate_hertz=48000,
        audio_channel_count=1,
    )
    return explicit, None


def _normalize_phrase(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > MAX_PHRASE_LENGTH:
        return None
    return text


_STRUCTURED_FIELDS = (
    "clientName",
    "client_name",
    "brandName",
    "brand_name",
    "projectName",
    "project_name",
)


def _collect_dynamic_hints(brief: Brief, seen: set[str]) -> list[str]:
    structured = (
        brief.structured_data if isinstance(brief.structured_data, dict) else {}
    )
    candidates: list[object] = [brief.title]
    candidates.extend(structured.get(field) for field in _STRUCTURED_FIELDS)
    result: list[str] = []
    for raw in candidates:
        normalized = _normalize_phrase(raw)
        if normalized is None:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _collect_static_hints(language: str, seen: set[str]) -> list[str]:
    prompt = BriefPrompt.objects.filter(
        slug=BriefPromptSlug.STT_INDUSTRY_TERMS,
        is_active=True,
    ).first()
    if prompt is None or not isinstance(prompt.metadata, dict):
        return []
    terms = prompt.metadata.get(language) or prompt.metadata.get("en") or []
    if not isinstance(terms, list):
        return []
    result: list[str] = []
    for raw in terms:
        normalized = _normalize_phrase(raw)
        if normalized is None:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _truncate_hints(
    dynamic_hints: list[str],
    static_hints: list[str],
) -> tuple[list[str], list[str]]:
    if len(dynamic_hints) + len(static_hints) <= MAX_PHRASE_HINTS:
        return dynamic_hints, static_hints
    if len(dynamic_hints) >= MAX_PHRASE_HINTS:
        return dynamic_hints[:MAX_PHRASE_HINTS], []
    return dynamic_hints, static_hints[: MAX_PHRASE_HINTS - len(dynamic_hints)]


def build_phrase_hints(brief: Brief, language: str) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    dynamic = _collect_dynamic_hints(brief, seen)
    static = _collect_static_hints(language, seen)
    return _truncate_hints(dynamic, static)


def _build_adaptation(
    dynamic_hints: list[str],
    static_hints: list[str],
) -> cloud_speech.SpeechAdaptation | None:
    phrases: list[cloud_speech.PhraseSet.Phrase] = [
        cloud_speech.PhraseSet.Phrase(value=x, boost=DYNAMIC_BOOST)
        for x in dynamic_hints
    ]
    phrases.extend(
        cloud_speech.PhraseSet.Phrase(value=x, boost=STATIC_BOOST) for x in static_hints
    )
    if not phrases:
        return None
    return cloud_speech.SpeechAdaptation(
        phrase_sets=[
            cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                inline_phrase_set=cloud_speech.PhraseSet(phrases=phrases),
            ),
        ],
    )


def _classify_invalid_argument(message: str) -> str:
    lowered = (message or "").lower()
    for keyword in _DURATION_KEYWORDS:
        if keyword in lowered:
            return ERROR_AUDIO_TOO_LONG
    return ERROR_UNSUPPORTED_FORMAT


def _build_recognition_config(
    mime: str,
    language: str,
    dynamic_hints: list[str],
    static_hints: list[str],
) -> cloud_speech.RecognitionConfig:
    explicit, auto = _build_decoding_config(mime)
    config_kwargs: dict = {
        "features": cloud_speech.RecognitionFeatures(enable_automatic_punctuation=True),
    }
    # With an explicit recognizer the model is baked into the recognizer
    # resource (e.g. chirp_3 in a regional location). Passing model here would
    # conflict; the synthetic "_" recognizer requires it inline. chirp_3 can
    # auto-detect the spoken language, so we use STT_LANGUAGE_CODES (default
    # "auto") instead of forcing the brief's document language.
    if STT_RECOGNIZER == "_":
        config_kwargs["model"] = STT_MODEL
        config_kwargs["language_codes"] = [language_to_bcp47(language)]
    else:
        config_kwargs["language_codes"] = STT_LANGUAGE_CODES
    if explicit is not None:
        config_kwargs["explicit_decoding_config"] = explicit
    if auto is not None:
        config_kwargs["auto_decoding_config"] = auto
    # Speech adaptation (phrase hints) is only supported by short/long/telephony
    # models. chirp_3 rejects a request carrying adaptation with a 404, so apply
    # phrase hints only for the synthetic "_" recognizer.
    if STT_RECOGNIZER == "_":
        adaptation = _build_adaptation(dynamic_hints, static_hints)
        if adaptation is not None:
            config_kwargs["adaptation"] = adaptation
    return cloud_speech.RecognitionConfig(**config_kwargs)


def _validate_input(audio_bytes: bytes, declared_mime: str) -> None:
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise TranscriptionError(ERROR_AUDIO_TOO_LARGE, f"size={len(audio_bytes)}")
    declared_normalized = (declared_mime or "").lower().strip()
    base_mime = _strip_mime_params(declared_mime)
    if (
        declared_normalized not in ALLOWED_AUDIO_MIMES
        and base_mime not in ALLOWED_AUDIO_MIMES
    ):
        raise TranscriptionError(ERROR_UNSUPPORTED_FORMAT, f"mime={declared_mime}")


def _call_recognize(
    audio_bytes: bytes,
    config: cloud_speech.RecognitionConfig,
) -> cloud_speech.RecognizeResponse:
    client = _get_speech_client()
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = _resolve_location()
    recognizer_path = (
        f"projects/{project}/locations/{location}/recognizers/{STT_RECOGNIZER}"
    )
    request = cloud_speech.RecognizeRequest(
        recognizer=recognizer_path,
        config=config,
        content=audio_bytes,
    )
    try:
        return client.recognize(request=request)
    except google_exceptions.ResourceExhausted as ex:
        logger.warning("STT quota exceeded: %s", ex)
        raise TranscriptionError(ERROR_QUOTA_EXCEEDED, str(ex)) from ex
    except google_exceptions.InvalidArgument as ex:
        code = _classify_invalid_argument(str(ex))
        logger.warning("STT invalid argument (%s): %s", code, ex)
        raise TranscriptionError(code, str(ex)) from ex
    except google_exceptions.GoogleAPICallError as ex:
        logger.exception("STT call error")
        raise TranscriptionError(ERROR_INTERNAL, str(ex)) from ex
    except Exception as ex:
        logger.exception("STT unexpected error")
        raise TranscriptionError(ERROR_INTERNAL, str(ex)) from ex


def _extract_transcript(response: cloud_speech.RecognizeResponse) -> str:
    parts: list[str] = []
    for result in response.results:
        if not result.alternatives:
            continue
        text = (result.alternatives[0].transcript or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def transcribe_audio(
    audio_bytes: bytes,
    declared_mime: str,
    language: str,
    dynamic_hints: list[str] | None = None,
    static_hints: list[str] | None = None,
) -> dict:
    _validate_input(audio_bytes, declared_mime)

    if os.environ.get("STT_DEV_FAKE") == "1":
        logger.info("STT_DEV_FAKE active; returning canned transcript")
        return {
            "text": "Hello, this is a fake test transcript for development.",
            "language": language_to_bcp47(language),
            "model": STT_MODEL,
        }

    config = _build_recognition_config(
        declared_mime,
        language,
        dynamic_hints or [],
        static_hints or [],
    )
    response = _call_recognize(audio_bytes, config)
    transcript = _extract_transcript(response)
    if not transcript:
        raise TranscriptionError(ERROR_NO_SPEECH, "empty transcript")

    return {
        "text": transcript,
        "language": language_to_bcp47(language),
        "model": STT_MODEL,
    }
