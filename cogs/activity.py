import logging

import discord
from discord.ext import commands, tasks

from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)


class Activity(commands.Cog):
    """Cog to handle user activity tracking and database updates."""

    def __init__(self, bot: KiwiBot) -> None:
        self.bot = bot
        self.activity_cache: set[int] = set()
        self.flush_activity_cache.start()

    def cog_unload(self) -> None:
        """Cancel the background task when the cog is unloaded."""
        self.flush_activity_cache.cancel()

    def _cache_user_activity(self, user: discord.User | discord.Member) -> None:
        """Add a user to the activity cache."""
        if user.bot:
            return

        self.activity_cache.add(user.id)
        log.debug("Cached activity for user %d", user.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen to messages to track user activity."""
        if message.guild:
            self._cache_user_activity(message.author)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Listen to interactions to track user activity."""
        if interaction.guild and interaction.user:
            self._cache_user_activity(interaction.user)

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
        except Exception:
            log.exception("Error in flush_activity_cache background task")


async def setup(bot: KiwiBot) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(Activity(bot))
