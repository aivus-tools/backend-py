"""Deploy-time system checks for the email agent.

A misconfigured ``FERNET_KEYS`` env var (missing, mismatched between
django/celeryworker/celerybeat, rotated without the old key) silently breaks
every encrypted credential the moment a worker tries to decrypt it, and the
agent stops polling with no visible cause. A registered ``deploy`` check turns
that into a hard failure at ``manage.py check --deploy`` time and at container
startup.
"""

from __future__ import annotations

from django.core.checks import Error
from django.core.checks import register
from django.db.utils import DatabaseError
from django.db.utils import OperationalError
from django.db.utils import ProgrammingError

_MISSING_KEYS_ID = "email_agent.E001"
_INVALID_KEYS_ID = "email_agent.E002"
_UNDECRYPTABLE_ID = "email_agent.E003"


@register(deploy=True)
def fernet_keys_configured(app_configs, **kwargs):
    """Fail deploy if ``FERNET_KEYS`` is missing, malformed, or key-rotated wrong.

    Runs only under ``--deploy`` so tests and local dev with an empty account
    table are not penalised. When live accounts exist, we prove one credential
    is actually decryptable with the current key ring — a rotation that dropped
    the old key would explode here rather than silently at poll time.
    """
    from django.conf import settings  # noqa: PLC0415

    errors: list[Error] = []
    keys = getattr(settings, "FERNET_KEYS", None) or []
    if not keys:
        errors.append(
            Error(
                "FERNET_KEYS is not configured; email agent cannot decrypt "
                "stored mailbox credentials.",
                id=_MISSING_KEYS_ID,
                hint="Set FERNET_KEYS in the environment (newest key first).",
            )
        )
        return errors

    from aivus_backend.email_agent.crypto import get_multifernet  # noqa: PLC0415

    try:
        get_multifernet.cache_clear()
        get_multifernet()
    except Exception as exc:
        errors.append(
            Error(
                f"FERNET_KEYS is malformed: {exc}",
                id=_INVALID_KEYS_ID,
                hint="Every key must be a url-safe base64-encoded 32-byte value.",
            )
        )
        return errors

    from aivus_backend.email_agent.crypto import decrypt  # noqa: PLC0415
    from aivus_backend.email_agent.models import EmailAccount  # noqa: PLC0415

    try:
        sample = (
            EmailAccount.objects.filter(deleted_at__isnull=True)
            .exclude(credential="")
            .values_list("credential", flat=True)
            .first()
        )
    except (DatabaseError, OperationalError, ProgrammingError):
        # No DB yet (migrations, fresh boot) or the table is missing; the
        # deploy check runs again after migrate, and other checks catch a broken
        # DB. Do not block deploy on transient DB unavailability.
        return errors

    if sample is None:
        return errors

    try:
        decrypt(sample)
    except Exception as exc:
        errors.append(
            Error(
                f"FERNET_KEYS cannot decrypt a stored mailbox credential: {exc}",
                id=_UNDECRYPTABLE_ID,
                hint=(
                    "A key was rotated without keeping the previous one, or the "
                    "key list differs between django/celeryworker/celerybeat. "
                    "Prepend the old key to FERNET_KEYS and re-run "
                    "reencrypt_secrets to migrate ciphertext to the new primary."
                ),
            )
        )
    return errors
