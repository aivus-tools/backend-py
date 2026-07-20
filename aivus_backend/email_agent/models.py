"""Email agent data models (Stage 3, PRD section 6).

Covers connected mailboxes, threads and messages, extracted action items, the
agent activity log, the per-vendor agent personality/settings, polymorphic
notification channels, and the outbound draft queue.
"""

from __future__ import annotations

import uuid

from django.db import models

from aivus_backend.email_agent.crypto import EncryptedTextField


class EmailProvider(models.TextChoices):
    GMAIL = "gmail", "Gmail"


class EmailAccountRole(models.TextChoices):
    MONITOR = "monitor", "Monitoring inbox (read-only)"
    AGENT = "agent", "Agent mailbox (sends)"


class EmailAccountStatus(models.TextChoices):
    CONNECTED = "connected", "Connected"
    EXPIRED = "expired", "Token expired"
    REVOKED = "revoked", "Access revoked"
    DISCONNECTED = "disconnected", "Disconnected"


class EmailDirection(models.TextChoices):
    IN = "in", "Inbound"
    OUT = "out", "Outbound"


class ThreadState(models.TextChoices):
    MONITORING = "monitoring", "Monitoring"
    ENGAGED = "engaged", "Engaged"
    PAUSED = "paused", "Paused"
    HUMAN_TAKEOVER = "human_takeover", "Human takeover"


class MessageIntent(models.TextChoices):
    ORDER = "order", "New order / lead"
    QUESTION = "question", "Question on current project"
    FOLLOW_UP = "follow_up", "Follow-up"
    EDITS = "edits", "Edits / changes"
    JUNK = "junk", "Junk / spam"
    AUTO_REPLY = "auto_reply", "Auto-reply / OOO"


class ActionAssignee(models.TextChoices):
    CLIENT = "client", "Client"
    PRODUCER = "producer", "Producer"
    AGENT = "agent", "Agent"


class ActionItemStatus(models.TextChoices):
    OPEN = "open", "Open"
    DONE = "done", "Done"
    OVERDUE = "overdue", "Overdue"


class AutonomyMode(models.TextChoices):
    DRAFT = "draft", "Draft plus approval"
    AUTO_SAFE = "auto_safe", "Auto-send safe replies"


class NotificationChannelType(models.TextChoices):
    EMAIL = "email", "Email"
    TELEGRAM = "telegram", "Telegram"
    DISCORD = "discord", "Discord"


class OutboundDraftStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    SENT = "sent", "Sent"
    EXPIRED = "expired", "Expired"
    REJECTED = "rejected", "Rejected"


class OutboundDraftKind(models.TextChoices):
    FIRST_REPLY = "first_reply", "First reply"
    FOLLOW_UP = "follow_up", "Follow-up"
    OTHER = "other", "Other"


class EmailAccount(models.Model):
    """A vendor's connected mailbox (agent or monitoring)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        "users.Vendor",
        on_delete=models.CASCADE,
        related_name="email_accounts",
    )
    provider = models.CharField(
        max_length=16,
        choices=EmailProvider.choices,
        default=EmailProvider.GMAIL,
    )
    role = models.CharField(max_length=16, choices=EmailAccountRole.choices)
    email = models.EmailField()
    credential = EncryptedTextField(blank=True, default="")
    scopes = models.JSONField(default=list, blank=True)
    uid_validity = models.CharField(max_length=64, blank=True, default="")
    last_seen_uid = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=16,
        choices=EmailAccountStatus.choices,
        default=EmailAccountStatus.CONNECTED,
    )
    next_poll_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "role"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_active_account_per_vendor_role",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_poll_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.role})"


class EmailThread(models.Model):
    """A conversation thread the agent monitors or drives."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        "users.Vendor",
        on_delete=models.CASCADE,
        related_name="email_threads",
    )
    provider_thread_id = models.CharField(max_length=998, db_index=True)
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_threads",
    )
    client_email = models.EmailField(blank=True, default="")
    client_name = models.CharField(max_length=255, blank=True, default="")
    canonical_subject = models.CharField(max_length=998, blank=True, default="")
    state = models.CharField(
        max_length=16,
        choices=ThreadState.choices,
        default=ThreadState.MONITORING,
    )
    state_before_pause = models.CharField(
        max_length=16,
        choices=ThreadState.choices,
        blank=True,
        default="",
    )
    paused_until = models.DateTimeField(null=True, blank=True)
    participants = models.JSONField(default=list, blank=True)
    memory = models.JSONField(default=dict, blank=True)
    last_history_id = models.CharField(max_length=64, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "provider_thread_id"],
                name="uniq_thread_per_vendor",
            ),
        ]

    def __str__(self) -> str:
        return f"Thread {self.provider_thread_id} ({self.state})"


class EmailMessage(models.Model):
    """A single message in a thread. Uniqueness is per-mailbox, not global."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        EmailAccount,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    thread = models.ForeignKey(
        EmailThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    provider_message_id = models.CharField(max_length=998)
    direction = models.CharField(max_length=8, choices=EmailDirection.choices)
    from_email = models.CharField(max_length=320, blank=True, default="")
    to_emails = models.JSONField(default=list, blank=True)
    cc_emails = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=998, blank=True, default="")
    body_clean = models.TextField(blank=True, default="")
    headers = models.JSONField(default=dict, blank=True)
    intent = models.CharField(
        max_length=16,
        choices=MessageIntent.choices,
        blank=True,
        default="",
    )
    is_auto_reply = models.BooleanField(default=False)
    message_id_header = models.CharField(max_length=998, blank=True, default="")
    in_reply_to = models.CharField(max_length=998, blank=True, default="")
    references = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "provider_message_id"],
                name="uniq_message_per_account",
            ),
        ]
        indexes = [
            models.Index(fields=["message_id_header"]),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.provider_message_id}"


class ActionItem(models.Model):
    """A promise or next step extracted from a thread."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        EmailThread,
        on_delete=models.CASCADE,
        related_name="action_items",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_action_items",
    )
    assignee = models.CharField(max_length=16, choices=ActionAssignee.choices)
    text = models.TextField()
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=ActionItemStatus.choices,
        default=ActionItemStatus.OPEN,
    )
    source_message = models.ForeignKey(
        EmailMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_items",
    )
    followup_count = models.PositiveSmallIntegerField(default=0)
    last_followup_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["assignee", "status", "due_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.assignee}: {self.text[:40]}"


def _email_attachment_upload_to(instance: EmailAttachment, filename: str) -> str:
    return f"email_agent/{instance.thread_id}/{instance.id}/{filename}"


class EmailAttachment(models.Model):
    """An inbound attachment, anchored to its message and thread.

    Stored on receipt even before a lead brief exists, so an attachment on a
    thread that has no project yet is not lost. When the thread becomes a lead,
    ``brief`` is linked so the brief pipeline can reach the files.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        EmailMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    thread = models.ForeignKey(
        EmailThread,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    brief = models.ForeignKey(
        "projects.Brief",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_attachments",
    )
    file = models.FileField(upload_to=_email_attachment_upload_to, max_length=500)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=128)
    size_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["thread", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.filename} ({self.mime_type})"


class AgentLog(models.Model):
    """Human-readable log of agent actions per thread/project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        EmailThread,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="logs",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_agent_logs",
    )
    event = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["thread", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event} @ {self.created_at:%Y-%m-%d %H:%M}"


class VendorAgentProfile(models.Model):
    """Per-vendor agent personality and settings (compiled system prompt)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.OneToOneField(
        "users.Vendor",
        on_delete=models.CASCADE,
        related_name="agent_profile",
    )
    system_prompt = models.TextField(blank=True, default="")
    business_context = models.TextField(blank=True, default="")
    tone = models.TextField(blank=True, default="")
    notification_rules = models.JSONField(default=dict, blank=True)
    special_rules = models.JSONField(default=list, blank=True)
    autonomy_mode = models.CharField(
        max_length=16,
        choices=AutonomyMode.choices,
        default=AutonomyMode.DRAFT,
    )
    producer_email = models.EmailField(blank=True, default="")
    working_hours = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Agent profile for {self.vendor_id}"


class NotificationChannel(models.Model):
    """A polymorphic delivery channel for producer notifications."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        "users.Vendor",
        on_delete=models.CASCADE,
        related_name="notification_channels",
    )
    type = models.CharField(
        max_length=16,
        choices=NotificationChannelType.choices,
        default=NotificationChannelType.EMAIL,
    )
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.type} channel for {self.vendor_id}"


class NotificationLog(models.Model):
    """Record of a delivered (or failed) producer notification."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        NotificationChannel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
    )
    vendor = models.ForeignKey(
        "users.Vendor",
        on_delete=models.CASCADE,
        related_name="notification_logs",
    )
    event = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    dedup_key = models.CharField(max_length=128, blank=True, default="")
    delivered = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")
    send_after = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["vendor", "event", "created_at"]),
            models.Index(fields=["delivered", "send_after"]),
        ]

    def __str__(self) -> str:
        return f"{self.event} ({'ok' if self.delivered else 'fail'})"


class OutboundDraft(models.Model):
    """A pending reply awaiting approval (draft mode) or auto-send."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        EmailThread,
        on_delete=models.CASCADE,
        related_name="drafts",
    )
    in_reply_to_message = models.ForeignKey(
        EmailMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drafts",
    )
    kind = models.CharField(
        max_length=16,
        choices=OutboundDraftKind.choices,
        default=OutboundDraftKind.OTHER,
    )
    body = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=OutboundDraftStatus.choices,
        default=OutboundDraftStatus.PENDING,
    )
    provider_draft_id = models.CharField(max_length=998, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["in_reply_to_message"],
                condition=models.Q(status=OutboundDraftStatus.PENDING),
                name="uniq_pending_draft_per_inbound",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} draft ({self.status})"
