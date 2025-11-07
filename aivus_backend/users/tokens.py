"""Token management for email confirmation and password reset."""

import secrets
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from django.db import models


class TokenType(models.TextChoices):
    """Token type choices."""

    EMAIL_CONFIRMATION = "EMAIL_CONFIRMATION", "Email Confirmation"
    PASSWORD_RESET = "PASSWORD_RESET", "Password Reset"


class AuthToken(models.Model):
    """
    Auth token for email confirmation and password reset.

    Tokens are stored in database and expire after 24 hours.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.CharField(max_length=255, unique=True, db_index=True)
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="auth_tokens",
    )
    token_type = models.CharField(max_length=20, choices=TokenType.choices)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auth_token"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.token_type} token for {self.user.email}"

    @staticmethod
    def generate_token() -> str:
        """Generate a secure random token."""
        return secrets.token_urlsafe(32)

    @classmethod
    def create_token(cls, user, token_type: TokenType, expires_in_hours: int = 24):
        """Create a new token for user."""
        token = cls.generate_token()
        expires_at = datetime.now(UTC) + timedelta(hours=expires_in_hours)

        return cls.objects.create(
            token=token,
            user=user,
            token_type=token_type,
            expires_at=expires_at,
        )

    def is_valid(self) -> bool:
        """Check if token is still valid."""
        return datetime.now(UTC) < self.expires_at

    @classmethod
    def cleanup_expired(cls):
        """Delete all expired tokens."""
        cls.objects.filter(expires_at__lt=datetime.now(UTC)).delete()
