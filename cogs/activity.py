import logging

import discord
from discord.ext import commands, tasks

from modules.CurrencyBot import CurrencyBot

log = logging.getLogger(__name__)


class Activity(commands.Cog):
    """Cog to handle user activity tracking and database updates."""

    def __init__(self, bot: CurrencyBot) -> None:
        self.bot = bot
        self.activity_cache: set[int] = set()
        self.flush_activity_cache.start()

    def cog_unload(self) -> None:
        """Cancel the background task when the cog is unloaded."""
        self.flush_activity_cache.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen to messages to track user activity."""
        if message.author.bot:
            return

        if message.guild:
            self.activity_cache.add(message.author.id)
            log.debug("Cached activity for user %d", message.author.id)

    @tasks.loop(seconds=60)
    async def flush_activity_cache(self) -> None:
        """Periodically flush the activity cache to the database."""
        if not self.activity_cache:
            return

        try:
            await self.bot.user_db.update_active_users(list(self.activity_cache))
            log.info(
                "Flushed %d user activities to database",
                len(self.activity_cache),
            )
            self.activity_cache.clear()
        except (discord.HTTPException, ConnectionError, OSError):
            log.exception("Error in flush_activity_cache background task")


async def setup(bot: CurrencyBot) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(Activity(bot))
