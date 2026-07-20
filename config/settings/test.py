"""
With these settings, tests run faster.
"""

from .base import *  # noqa: F403
from .base import TEMPLATES
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="e4YhAy8pSBg0Unp2HTLh4LmsJaiO4bHQiYF8ZhPZW2q9PJQaVgTVR3dld7iZFIbl",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# EMAIL AGENT (Stage 3): deterministic Fernet key for encrypted fields in tests
FERNET_KEYS = env.list(
    "FERNET_KEYS",
    default=["Qr0kH-tgQGFEG9t96ad3PtnvA2zeuaMf_3jnTEHbqDg="],
)

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore[index]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "http://media.testserver/"
# HMAC / API KEY
# QA3-025: Provide defaults so tests run outside Docker
# ------------------------------------------------------------------------------
HMAC_SECRET = env("HMAC_SECRET", default="test-hmac-secret")
API_KEY = env("API_KEY", default="test-api-key")

# RATE LIMITING
# ------------------------------------------------------------------------------
# Disable rate limiting in tests to prevent test interference
RATELIMIT_ENABLE = False

# CUSTOM AI INSTRUCTIONS GUARD
# ------------------------------------------------------------------------------
# Keep the save-time LLM judge off so tests stay offline; heuristics still run.
CUSTOM_AI_INSTRUCTIONS_JUDGE_ENABLED = False

# Your stuff...
# ------------------------------------------------------------------------------
