from django.contrib import admin

from .models import BoostRequest, GuildConfig, NewsPost


@admin.register(GuildConfig)
class GuildConfigAdmin(admin.ModelAdmin):
    list_display = ("guild_name", "guild_id", "request_channel_id", "news_channel_id")


@admin.register(BoostRequest)
class BoostRequestAdmin(admin.ModelAdmin):
    list_display = ("full_name", "kind", "status", "discord_username", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("full_name", "discord_username")


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    list_display = ("title", "guild_id", "posted_at")
