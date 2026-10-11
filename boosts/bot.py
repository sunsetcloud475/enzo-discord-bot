import logging
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from django.conf import settings

from .models import BoostRequest, GuildConfig, NewsPost, PanelMessage
from .news import fetch_news

log = logging.getLogger("wowboost")

STATUS_COLORS = {
    BoostRequest.Status.PENDING: discord.Color.gold(),
    BoostRequest.Status.APPROVED: discord.Color.green(),
    BoostRequest.Status.REJECTED: discord.Color.red(),
}
MAX_NEWS_PER_CYCLE = 5


def valid_url(value: str) -> bool:
    p = urlparse(value.strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


PANEL_TITLES = {
    "dungeon": "Request a dungeon boost",
    "raid": "Request a raid boost",
    "both": "Request a WoW boost",
}


def panel_embed(kind: str) -> discord.Embed:
    return discord.Embed(
        title=PANEL_TITLES[kind],
        description=(
            "Click the button below, enter your full name and a link to "
            "your resume, and our team will review your request."
        ),
        color=discord.Color.blurple(),
    )


def request_embed(req: BoostRequest) -> discord.Embed:
    embed = discord.Embed(
        title=f"{req.get_kind_display()} boost request",
        color=STATUS_COLORS[req.status],
        timestamp=req.created_at,
    )
    embed.add_field(name="Name", value=req.full_name, inline=True)
    embed.add_field(name="Discord", value=f"<@{req.discord_user_id}>", inline=True)
    embed.add_field(name="Resume", value=req.resume_url, inline=False)
    status = req.get_status_display()
    if req.reviewed_by:
        status += f" by {req.reviewed_by}"
    embed.add_field(name="Status", value=status, inline=False)
    embed.set_footer(text=f"Request #{req.pk}")
    return embed


class ReviewView(discord.ui.View):
    """Persistent Approve/Reject buttons (work after bot restarts)."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _review(self, interaction: discord.Interaction, status):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the Manage Server permission to review requests.",
                ephemeral=True,
            )
            return
        req = await BoostRequest.objects.filter(
            message_id=interaction.message.id
        ).afirst()
        if req is None:
            await interaction.response.send_message(
                "Couldn't find this request in the database.", ephemeral=True
            )
            return
        req.status = status
        req.reviewed_by = str(interaction.user)
        await req.asave(update_fields=["status", "reviewed_by"])
        await interaction.response.edit_message(embed=request_embed(req), view=None)

    @discord.ui.button(
        label="Approve", style=discord.ButtonStyle.success, custom_id="boost:approve"
    )
    async def approve(self, interaction, button):
        await self._review(interaction, BoostRequest.Status.APPROVED)

    @discord.ui.button(
        label="Reject", style=discord.ButtonStyle.danger, custom_id="boost:reject"
    )
    async def reject(self, interaction, button):
        await self._review(interaction, BoostRequest.Status.REJECTED)


class BoostModal(discord.ui.Modal):
    full_name = discord.ui.TextInput(label="Full name", max_length=100)
    resume = discord.ui.TextInput(
        label="Link to your resume", placeholder="https://...", max_length=500
    )

    def __init__(self, kind: str):
        super().__init__(title=f"{BoostRequest.Kind(kind).label} boost request")
        self.kind = kind

    async def on_submit(self, interaction: discord.Interaction):
        if not valid_url(self.resume.value):
            await interaction.response.send_message(
                "That resume link isn't a valid http(s) URL. Please try again.",
                ephemeral=True,
            )
            return

        # Requests are posted in the channel where the button was clicked.
        channel = interaction.channel

        await interaction.response.defer(ephemeral=True)
        req = await BoostRequest.objects.acreate(
            guild_id=interaction.guild_id,
            discord_user_id=interaction.user.id,
            discord_username=str(interaction.user),
            full_name=self.full_name.value.strip(),
            resume_url=self.resume.value.strip(),
            kind=self.kind,
        )
        try:
            msg = await channel.send(embed=request_embed(req), view=ReviewView())
        except discord.Forbidden:
            await interaction.followup.send(
                "I couldn't post your request in this channel. "
                "Please tell an admin to check my permissions.",
                ephemeral=True,
            )
            return
        req.message_id = msg.id
        await req.asave(update_fields=["message_id"])
        await interaction.followup.send(
            f"Your request was submitted to {channel.mention}.", ephemeral=True
        )


class PanelView(discord.ui.View):
    """Persistent buttons posted by /setup_requests."""

    def __init__(self, kind: str = "both"):
        super().__init__(timeout=None)
        if kind == "dungeon":
            self.remove_item(self.raid)
        elif kind == "raid":
            self.remove_item(self.dungeon)

    @discord.ui.button(
        label="Dungeon boost",
        style=discord.ButtonStyle.primary,
        custom_id="boost:panel:dungeon",
    )
    async def dungeon(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(BoostModal("dungeon"))

    @discord.ui.button(
        label="Raid boost",
        style=discord.ButtonStyle.primary,
        custom_id="boost:panel:raid",
    )
    async def raid(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(BoostModal("raid"))


class WowBoostBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.http_session: aiohttp.ClientSession | None = None
        self._register_commands()

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        self.add_view(ReviewView())
        self.add_view(PanelView())
        await self.tree.sync()
        self.news_loop.change_interval(minutes=settings.NEWS_POLL_MINUTES)
        self.news_loop.start()
        self.panel_loop.change_interval(minutes=settings.PANEL_REFRESH_MINUTES)
        self.panel_loop.start()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def on_ready(self):
        log.info("Logged in as %s", self.user)

    # ---- commands -------------------------------------------------------
    def _register_commands(self):
        tree = self.tree

        @tree.command(
            name="setup_requests",
            description="Post a boost request button in a channel",
        )
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.choices(
            kind=[
                app_commands.Choice(name="Dungeon button", value="dungeon"),
                app_commands.Choice(name="Raid button", value="raid"),
                app_commands.Choice(name="Both buttons", value="both"),
            ]
        )
        async def setup_requests(
            interaction: discord.Interaction,
            channel: discord.TextChannel,
            kind: app_commands.Choice[str],
        ):
            await interaction.response.defer(ephemeral=True)
            try:
                msg = await channel.send(
                    embed=panel_embed(kind.value), view=PanelView(kind.value)
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    f"I can't send messages in {channel.mention}. Give me Send "
                    "Messages and Embed Links there, then run this again.",
                    ephemeral=True,
                )
                return
            # Remember the panel (for refreshing) and remove any older one
            # in this channel.
            existing = await PanelMessage.objects.filter(channel_id=channel.id).afirst()
            if existing:
                try:
                    await channel.get_partial_message(existing.message_id).delete()
                except discord.HTTPException:
                    pass
                existing.message_id = msg.id
                existing.kind = kind.value
                await existing.asave(update_fields=["message_id", "kind"])
            else:
                await PanelMessage.objects.acreate(
                    guild_id=interaction.guild_id,
                    channel_id=channel.id,
                    message_id=msg.id,
                    kind=kind.value,
                )
            await interaction.followup.send(
                f"Panel posted in {channel.mention}. Requests will appear there.",
                ephemeral=True,
            )

        @tree.command(
            name="setup_news", description="Choose the channel for WoW news"
        )
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def setup_news(
            interaction: discord.Interaction, channel: discord.TextChannel
        ):
            await GuildConfig.objects.aupdate_or_create(
                guild_id=interaction.guild_id,
                defaults={
                    "guild_name": interaction.guild.name,
                    "news_channel_id": channel.id,
                },
            )
            await interaction.response.send_message(
                f"WoW news will be posted in {channel.mention}.", ephemeral=True
            )

    # ---- panel refresher ------------------------------------------------
    @tasks.loop(minutes=30)
    async def panel_loop(self):
        """Move each button panel to the bottom of its channel."""
        async for panel in PanelMessage.objects.all():
            channel = self.get_channel(panel.channel_id)
            if channel is None:
                continue
            if channel.last_message_id == panel.message_id:
                continue  # already the newest message, nothing to do
            try:
                new = await channel.send(
                    embed=panel_embed(panel.kind), view=PanelView(panel.kind)
                )
            except discord.HTTPException:
                log.exception("Couldn't refresh panel in %s", panel.channel_id)
                continue
            try:
                await channel.get_partial_message(panel.message_id).delete()
            except discord.HTTPException:
                pass  # old one already gone
            panel.message_id = new.id
            await panel.asave(update_fields=["message_id"])

    @panel_loop.before_loop
    async def before_panel(self):
        await self.wait_until_ready()

    # ---- news poller ----------------------------------------------------
    @tasks.loop(minutes=10)
    async def news_loop(self):
        try:
            items = await fetch_news(self.http_session)
        except Exception:
            log.exception("Failed to fetch news")
            return

        async for cfg in GuildConfig.objects.filter(news_channel_id__isnull=False):
            channel = self.get_channel(cfg.news_channel_id)
            if channel is None:
                continue
            posted = {
                pid
                async for pid in NewsPost.objects.filter(
                    guild_id=cfg.guild_id
                ).values_list("external_id", flat=True)
            }
            fresh = [i for i in items if i["id"] not in posted][:MAX_NEWS_PER_CYCLE]
            for item in reversed(fresh):  # oldest first
                embed = discord.Embed(
                    title=item["title"],
                    url=item["url"] or None,
                    description=item["summary"] or None,
                    color=discord.Color.blue(),
                )
                if item["image"]:
                    embed.set_image(url=item["image"])
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    log.exception("Couldn't post news to %s", cfg.guild_id)
                    break
                await NewsPost.objects.aget_or_create(
                    guild_id=cfg.guild_id,
                    external_id=item["id"],
                    defaults={"title": item["title"][:300]},
                )

    @news_loop.before_loop
    async def before_news(self):
        await self.wait_until_ready()
