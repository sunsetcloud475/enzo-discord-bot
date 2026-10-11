from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from boosts.bot import WowBoostBot


class Command(BaseCommand):
    help = "Run the WoW boost Discord bot"

    def handle(self, *args, **options):
        if not settings.DISCORD_TOKEN:
            raise CommandError("DISCORD_TOKEN is not set (see .env.example).")
        WowBoostBot().run(settings.DISCORD_TOKEN)
