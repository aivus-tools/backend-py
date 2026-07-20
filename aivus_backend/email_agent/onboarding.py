"""Agent onboarding: persist the vendor's free-text instruction and settings.

MVP onboarding is a single large instruction plus a few settings, not a chat
interview (S3-36). The instruction is compiled straight into the classification,
reply and follow-up system prompts, so it is untrusted vendor input and runs the
same save-time injection guard the brief assistant uses. Everything else is
validated here before it can steer the agent: a bad timezone or an auto-send mode
must be refused, not silently accepted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from aivus_backend.core.prompt_guard import screen_custom_ai_instructions
from aivus_backend.email_agent.models import AutonomyMode
from aivus_backend.email_agent.notifications import NOTIFICATION_MODE_EVERY
from aivus_backend.email_agent.notifications import NOTIFICATION_MODE_URGENT_AND_DIGEST

if TYPE_CHECKING:
    from collections.abc import Callable

    from aivus_backend.email_agent.models import VendorAgentProfile

INSTRUCTION_MAX_LENGTH = 8000
CONTEXT_MAX_LENGTH = 4000
TONE_MAX_LENGTH = 2000
MAX_SPECIAL_RULES = 50
RULE_MAX_LENGTH = 500

_NOTIFICATION_MODES = {NOTIFICATION_MODE_EVERY, NOTIFICATION_MODE_URGENT_AND_DIGEST}
_HHMM_LENGTH = 5
_MAX_HOUR = 23
_MAX_MINUTE = 59
_DAY_RANGE = range(1, 8)
# Matches the EmailField column width; a longer-but-valid address would pass
# Django's validator (it caps at 320) and then 500 on save into varchar(254).
_PRODUCER_EMAIL_MAX_LENGTH = 254

_INJECTION_ERROR = (
    "These instructions look unsafe (a possible attempt to override or extract "
    "the assistant's rules) and were not saved. Please rephrase them as tone, "
    "style, or business guidance."
)


class ProfileValidationError(Exception):
    """A profile update was rejected; the message is safe to show the vendor."""


def _as_text(value: object, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        msg = f"{field} must be a string"
        raise ProfileValidationError(msg)
    text = value.strip()
    if len(text) > max_length:
        msg = f"{field} must be {max_length} characters or fewer"
        raise ProfileValidationError(msg)
    return text


def _valid_hhmm(value: object) -> bool:
    if not isinstance(value, str) or len(value) != _HHMM_LENGTH or value[2] != ":":
        return False
    hours, minutes = value[:2], value[3:]
    if not (hours.isdigit() and minutes.isdigit()):
        return False
    return 0 <= int(hours) <= _MAX_HOUR and 0 <= int(minutes) <= _MAX_MINUTE


def _clean_working_hours(value: object) -> dict:
    if not isinstance(value, dict):
        msg = "workingHours must be an object"
        raise ProfileValidationError(msg)
    cleaned: dict = {}
    timezone_name = value.get("timezone")
    if timezone_name:
        try:
            ZoneInfo(str(timezone_name))
        except (ZoneInfoNotFoundError, ValueError) as error:
            msg = "workingHours.timezone is not a valid IANA timezone"
            raise ProfileValidationError(msg) from error
        cleaned["timezone"] = str(timezone_name)
    for bound in ("start", "end"):
        raw = value.get(bound)
        if raw:
            if not _valid_hhmm(raw):
                msg = f"workingHours.{bound} must be HH:MM"
                raise ProfileValidationError(msg)
            cleaned[bound] = raw
    days = value.get("days")
    if days is not None:
        if not isinstance(days, list) or any(day not in _DAY_RANGE for day in days):
            msg = "workingHours.days must be a list of ISO weekday numbers (1-7)"
            raise ProfileValidationError(msg)
        cleaned["days"] = sorted({int(day) for day in days})
    return cleaned


def _clean_special_rules(value: object) -> list[str]:
    if not isinstance(value, list):
        msg = "specialRules must be a list of strings"
        raise ProfileValidationError(msg)
    if len(value) > MAX_SPECIAL_RULES:
        msg = f"specialRules must have {MAX_SPECIAL_RULES} entries or fewer"
        raise ProfileValidationError(msg)
    rules: list[str] = []
    for entry in value:
        rule = _as_text(entry, "specialRules entry", RULE_MAX_LENGTH)
        if rule:
            rules.append(rule)
    return rules


def _clean_notification_rules(value: object) -> dict:
    if not isinstance(value, dict):
        msg = "notificationRules must be an object"
        raise ProfileValidationError(msg)
    mode = value.get("mode", NOTIFICATION_MODE_EVERY)
    if mode not in _NOTIFICATION_MODES:
        allowed = ", ".join(sorted(_NOTIFICATION_MODES))
        msg = f"notificationRules.mode must be one of: {allowed}"
        raise ProfileValidationError(msg)
    return {"mode": mode}


def _apply_instruction(profile: VendorAgentProfile, value: object) -> None:
    instruction = _as_text(value, "instruction", INSTRUCTION_MAX_LENGTH)
    if instruction and instruction != profile.system_prompt:
        verdict = screen_custom_ai_instructions(instruction)
        if not verdict.safe:
            raise ProfileValidationError(_INJECTION_ERROR)
    profile.system_prompt = instruction


def _apply_business_context(profile: VendorAgentProfile, value: object) -> None:
    profile.business_context = _as_text(value, "businessContext", CONTEXT_MAX_LENGTH)


def _apply_tone(profile: VendorAgentProfile, value: object) -> None:
    profile.tone = _as_text(value, "tone", TONE_MAX_LENGTH)


def _apply_special_rules(profile: VendorAgentProfile, value: object) -> None:
    profile.special_rules = _clean_special_rules(value)


def _apply_producer_email(profile: VendorAgentProfile, value: object) -> None:
    email = _as_text(value, "producerEmail", _PRODUCER_EMAIL_MAX_LENGTH)
    if email:
        try:
            validate_email(email)
        except ValidationError as error:
            msg = "producerEmail is not a valid email address"
            raise ProfileValidationError(msg) from error
    profile.producer_email = email


def _apply_working_hours(profile: VendorAgentProfile, value: object) -> None:
    profile.working_hours = _clean_working_hours(value)


def _apply_notification_rules(profile: VendorAgentProfile, value: object) -> None:
    profile.notification_rules = _clean_notification_rules(value)


def _apply_autonomy_mode(profile: VendorAgentProfile, value: object) -> None:
    # MVP is draft-only; auto-send is a reserved seam, never vendor-selectable.
    if value != AutonomyMode.DRAFT:
        msg = "autonomyMode must be 'draft' in this release"
        raise ProfileValidationError(msg)
    profile.autonomy_mode = AutonomyMode.DRAFT


_FIELD_APPLIERS: tuple[
    tuple[str, Callable[[VendorAgentProfile, object], None]], ...
] = (
    ("instruction", _apply_instruction),
    ("businessContext", _apply_business_context),
    ("tone", _apply_tone),
    ("specialRules", _apply_special_rules),
    ("producerEmail", _apply_producer_email),
    ("workingHours", _apply_working_hours),
    ("notificationRules", _apply_notification_rules),
    ("autonomyMode", _apply_autonomy_mode),
)


def apply_profile_update(profile: VendorAgentProfile, data: dict) -> None:
    """Validate and apply a partial profile update; raise on any invalid field.

    Only the keys present in ``data`` are touched, so a partial save never wipes a
    field the vendor did not send.
    """
    if not isinstance(data, dict):
        msg = "Request body must be a JSON object"
        raise ProfileValidationError(msg)
    for key, applier in _FIELD_APPLIERS:
        if key in data:
            applier(profile, data[key])
