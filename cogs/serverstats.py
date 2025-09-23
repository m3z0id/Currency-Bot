import logging
import os

import discord
from discord.ext import commands, tasks

# Set up basic logging
log = logging.getLogger(__name__)

# --- Configuration ---
try:
    TAG_ROLE_ID = int(os.getenv("TAG_ROLE_ID"))
    log.info("Successfully loaded TAG_ROLE_ID: %s", TAG_ROLE_ID)
except (TypeError, ValueError):
    TAG_ROLE_ID = None
    log.warning(
        "TAG_ROLE_ID is not set in your environment variables or is invalid. 'Tag Users' counter will be disabled.",
    )

UPDATE_INTERVAL_MINUTES = 5


class ServerStats(commands.Cog):
    """A cog that automatically updates server statistics in designated voice channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.update_stats.start()

    def cog_unload(self) -> None:
        """Clean up when the cog is unloaded."""
        self.update_stats.cancel()

    @tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
    async def update_stats(self) -> None:
        """Update the stats for all guilds."""
        log.info("Starting scheduled server stats update...")
        for guild in self.bot.guilds:
            await self._update_guild_stats(guild)
        log.info("Finished scheduled server stats update.")

    async def _update_guild_stats(self, guild: discord.Guild) -> None:
        """Handle the statistics update for a single guild."""
        log.info("Processing guild: %s (%s)", guild.name, guild.id)

        # Count members who have at least one role
        all_members_count = len(
            [m for m in guild.members if not m.bot and m.flags.completed_onboarding and len(m.roles) > 1],
        )

        tag_members_count = 0
        if TAG_ROLE_ID:
            tag_role = guild.get_role(TAG_ROLE_ID)
            if tag_role:
                tag_members_count = len(tag_role.members)
            else:
                log.warning(
                    "Could not find role with ID %s in guild '%s'.",
                    TAG_ROLE_ID,
                    guild.name,
                )

        # Iterate through all channels to find stat channels
        for channel in guild.channels:
            # We only care about voice channels as they are commonly used for stats
            if not isinstance(channel, discord.VoiceChannel):
                continue

            # Check if the channel is a "private" stats channel
            perms = channel.permissions_for(guild.default_role)
            if not (perms.view_channel and not perms.connect):
                continue

            # Update the channel name if necessary
            try:
                if channel.name.startswith("All members:"):
                    new_name = f"All members: {all_members_count}"
                    if channel.name != new_name:
                        await channel.edit(
                            name=new_name,
                            reason="Automated server stats update",
                        )
                        log.info(
                            "Updated 'All members' count for '%s' to %s.",
                            guild.name,
                            all_members_count,
                        )

                elif channel.name.startswith("Tag Users:") and TAG_ROLE_ID:
                    new_name = f"Tag Users: {tag_members_count}"
                    if channel.name != new_name:
                        await channel.edit(
                            name=new_name,
                            reason="Automated server stats update",
                        )
                        log.info(
                            "Updated 'Tag Users' count for '%s' to %s.",
                            guild.name,
                            tag_members_count,
                        )

            except discord.Forbidden:
                log.exception(
                    "Missing 'Manage Channels' permission in guild '%s' to update stats.",
                    guild.name,
                )
                # We can break here since we likely can't edit any channels in this guild
                break
            except discord.HTTPException:
                log.exception(
                    "An HTTP error occurred while updating channel in '%s'",
                    guild.name,
                )


async def setup(bot: commands.Bot) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(ServerStats(bot))
