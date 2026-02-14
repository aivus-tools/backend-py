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

# Your stuff...
# ------------------------------------------------------------------------------
