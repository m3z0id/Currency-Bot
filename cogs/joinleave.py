import datetime
import logging
import os
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from modules.CurrencyBot import CurrencyBot

log = logging.getLogger(__name__)


class JoinLeaveLogCog(commands.Cog):
    """A cog for logging member join and leave events to a specified channel."""

    def __init__(self, bot: "CurrencyBot", channel_id: int) -> None:
        self.bot = bot
        self.channel_id = channel_id
        self.log_channel: discord.TextChannel | None = None

    async def cog_load(self) -> None:
        """Fetch the channel object when the cog is loaded."""
        channel = self.bot.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            self.log_channel = channel
            log.info("Join/Leave logging channel set to #%s", self.log_channel.name)
        else:
            log.error(
                "Could not find the JOIN_LEAVE_LOG_CHANNEL_ID channel or it is not a text channel.",
            )

    async def _log_event(
        self,
        member: discord.Member,
        title: str,
        color: discord.Color,
        description_parts: list[str],
    ) -> None:
        """Construct and sends a standardized embed for join/leave events."""
        if not self.log_channel:
            log.warning("Log channel not available, cannot send join/leave log.")
            return

        embed = discord.Embed(
            color=color,
            timestamp=datetime.datetime.now(datetime.UTC),
            description="\n".join(description_parts),
        )

        # Use member's display name and avatar for the author field
        embed.set_author(
            name=f"{member.name} ({member.display_name})",
            icon_url=member.display_avatar.url,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.title = title

        try:
            # Defensively disable all pings. Only display mentions.
            await self.log_channel.send(embed=embed, allowed_mentions=None)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Failed to send message to join/leave log channel")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Handle logging when a new member joins or rejoins the server."""
        # Use the did_rejoin flag to determine the event type
        if member.flags.did_rejoin:
            title = "Member Rejoined"
            color = discord.Color.blue()
        else:
            title = "Member Joined"
            color = discord.Color.green()

        if member.bot:
            title += " [BOT]"

        # Prepare description lines for the embed
        date = int(member.created_at.timestamp())
        description = [
            f"{member.mention} was the **{member.guild.member_count}th** member to join.",
            f"Account created: <t:{date}:F> <t:{date}:R>",
        ]

        await self._log_event(member, title, color, description)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Handle logging when a member leaves the server."""
        title = "Member Left"
        color = discord.Color.orange()

        if member.bot:
            title += " [BOT]"

        # Format the roles the member had
        roles = [r.mention for r in member.roles if r.id != member.guild.default_role.id]
        roles_str = " ".join(roles) if roles else "None"

        # Prepare description lines
        description = [
            f"{member.mention} has left the server.",
            f"**Roles:** {roles_str}",
        ]

        await self._log_event(member, title, color, description)


async def setup(bot: "CurrencyBot") -> None:
    """Add the cog to the bot."""
    channel_id_str = os.getenv("JOIN_LEAVE_LOG_CHANNEL_ID")
    if not channel_id_str:
        log.warning(
            "JOIN_LEAVE_LOG_CHANNEL_ID environment variable not set. JoinLeaveLogCog will not be loaded.",
        )
        return

    try:
        channel_id = int(channel_id_str)
        await bot.add_cog(JoinLeaveLogCog(bot, channel_id))
    except ValueError:
        log.exception(
            "JOIN_LEAVE_LOG_CHANNEL_ID is not a valid integer. JoinLeaveLogCog will not be loaded.",
        )
