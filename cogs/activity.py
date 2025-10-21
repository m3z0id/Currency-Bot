import logging
from typing import override

import discord
from discord.ext import commands, tasks

from modules.dtypes import GuildId, UserGuildPair, UserId
from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)


class Activity(commands.Cog):
    """Handle user activity tracking and database updates."""

    def __init__(self, bot: KiwiBot) -> None:
        self.bot = bot
        self.activity_cache: set[UserGuildPair] = set()
        self.flush_activity_cache.start()

    @override
    def cog_unload(self) -> None:
        """Cancel the background task when the cog is unloaded."""
        self.flush_activity_cache.cancel()

    def _cache_user_activity(self, user: discord.User | discord.Member, guild_id: GuildId) -> None:
        """Add a user and their guild to the activity cache."""
        if user.bot:
            return

        self.activity_cache.add((UserId(user.id), guild_id))
        log.debug("Cached activity for user %d in guild %d", user.id, guild_id)

    @commands.Cog.listener()
    @override
    async def on_message(self, message: discord.Message) -> None:
        """Listen to messages to track user activity."""
        if message.guild:
            self._cache_user_activity(message.author, GuildId(message.guild.id))

    @commands.Cog.listener()
    @override
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Listen to interactions to track user activity."""
        if interaction.guild and interaction.user:
            self._cache_user_activity(interaction.user, GuildId(interaction.guild.id))

    @tasks.loop(seconds=60)
    async def flush_activity_cache(self) -> None:
        """Periodically flush the activity cache to the database."""
        if not self.activity_cache:
            return

        # We need to know which guilds to log to *before* we clear the cache
        guild_ids_in_batch = {guild_id for _, guild_id in self.activity_cache}

        try:
            await self.bot.user_db.update_active_users(list(self.activity_cache))
            log.debug(
                "Flushed %d user activities to database",
                len(self.activity_cache),
            )
        except Exception:
            log.exception("Error in flush_activity_cache background task")
            for guild_id in guild_ids_in_batch:
                await self.bot.log_admin_warning(
                    guild_id=guild_id,
                    warning_type="db_flush_fail",
                    description=(
                        "An error occurred in the `flush_activity_cache` background task. "
                        "User activity is not being saved. This may be a database issue. "
                        "Check console logs for details."
                    ),
                    level="ERROR",
                )
        finally:
            self.activity_cache.clear()


async def setup(bot: KiwiBot) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(Activity(bot))
