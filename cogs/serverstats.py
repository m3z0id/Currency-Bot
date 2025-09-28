import logging

import discord
from discord.ext import commands, tasks

# Set up basic logging
log = logging.getLogger(__name__)

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
        for guild in self.bot.guilds:
            await self._update_guild_stats(guild)

    async def _update_guild_stats(self, guild: discord.Guild) -> None:
        """Handle the statistics update for a single guild."""
        for role in guild.roles:
            if role.name == "Tag Users":
                tag_members_count = len(role.members)
                break
        else:
            log.debug("Could not find tag role in guild '%s'.", guild.name)
            tag_members_count = 0

        # Count members who have at least one role (failed or passed captcha)
        members_count = len(
            [m for m in guild.members if not m.bot and m.flags.completed_onboarding and len(m.roles) > 1],
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
                    new_name = f"All members: {members_count}"
                    if channel.name != new_name:
                        await channel.edit(
                            name=new_name,
                            reason="Automated server stats update",
                        )
                        log.info(
                            "Updated 'All members' count for '%s' to %s.",
                            guild.name,
                            members_count,
                        )

                elif channel.name.startswith("Tag Users:"):
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
