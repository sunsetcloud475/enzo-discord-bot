from django.db import models


class GuildConfig(models.Model):
    """Per-server channel settings, set with /setup_requests and /setup_news."""

    guild_id = models.BigIntegerField(unique=True)
    guild_name = models.CharField(max_length=100, blank=True)
    request_channel_id = models.BigIntegerField(
        null=True, blank=True, help_text="Channel where boost requests are posted"
    )
    news_channel_id = models.BigIntegerField(
        null=True, blank=True, help_text="Channel where WoW news is posted"
    )

    def __str__(self):
        return self.guild_name or str(self.guild_id)


class BoostRequest(models.Model):
    class Kind(models.TextChoices):
        DUNGEON = "dungeon", "Dungeon"
        RAID = "raid", "Raid"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    guild_id = models.BigIntegerField(db_index=True)
    discord_user_id = models.BigIntegerField()
    discord_username = models.CharField(max_length=100)
    full_name = models.CharField(max_length=100)
    resume_url = models.URLField(max_length=500)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    message_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    reviewed_by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.get_kind_display()}) - {self.status}"


class NewsPost(models.Model):
    """Tracks which news items were already posted so nothing is duplicated."""

    guild_id = models.BigIntegerField()
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=300, blank=True)
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["guild_id", "external_id"], name="unique_news_per_guild"
            )
        ]


class PanelMessage(models.Model):
    """The button panel posted by /setup_requests, so it can be refreshed."""

    guild_id = models.BigIntegerField()
    channel_id = models.BigIntegerField(unique=True)
    message_id = models.BigIntegerField()
    kind = models.CharField(max_length=10, default="both")
