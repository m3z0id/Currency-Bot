import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from modules.types import ChannelId, GuildId

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)


class JoinLeaveLogCog(commands.Cog):
    """A cog for logging member join and leave events to a specified channel."""

    def __init__(self, bot: "KiwiBot", guild_id: GuildId, channel_id: ChannelId) -> None:
        self.bot = bot
        self.privileged_guild_id = guild_id
        self.channel_id = channel_id
        self.log_channel: discord.TextChannel | None = None

    async def cog_load(self) -> None:
        """Fetch the channel object when the cog is loaded."""
        # Only attempt to fetch the channel if we are in the privileged guild context
        guild = await self.bot.fetch_guild(self.privileged_guild_id)
        if not guild:
            return

        channel = await self.bot.fetch_channel(self.channel_id)
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
        color: discord.Colour,
        description_parts: list[str],
    ) -> None:
        """Construct and sends a standardized embed for join/leave events."""
        if not self.log_channel:
            log.warning("Log channel not available, cannot send join/leave log.")
            return

        embed = discord.Embed(
            color=color,
            timestamp=discord.utils.utcnow(),
            description="\n".join(description_parts),
        )

        # Use member's display name and avatar for the author field
        embed.set_author(
            name=f"{member.name} ({member.display_name})",
            icon_url=member.display_avatar,
        )
        embed.set_thumbnail(url=member.display_avatar)
        embed.title = title

        try:
            # Defensively disable all pings. Only display mentions.
            await self.log_channel.send(embed=embed, allowed_mentions=None)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Failed to send message to join/leave log channel")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Handle logging when a new member joins or rejoins the server."""
        if member.guild.id != self.privileged_guild_id:
            return

        # Use the did_rejoin flag to determine the event type
        if member.flags.did_rejoin:
            title = "Member Rejoined"
            color = discord.Colour.blue()
        else:
            title = "Member Joined"
            color = discord.Colour.green()

        if member.bot:
            title += " [BOT]"

        # Count members who have at least one role (failed or passed captcha)
        member_count = len(
            [m for m in member.guild.members if not m.bot and m.flags.completed_onboarding and len(m.roles) > 1],
        )

        # Prepare description lines for the embed
        description = [
            f"{member.mention} was the **{member_count}th** member to join.",
            f"Account created: {discord.utils.format_dt(member.created_at, 'F')} \
({discord.utils.format_dt(member.created_at, 'R')})",
        ]

        await self._log_event(member, title, color, description)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Handle logging when a member leaves the server."""
        if member.guild.id != self.privileged_guild_id:
            return

        title = "Member Left"
        color = discord.Colour.orange()

        if member.bot:
            title += " [BOT]"

        # Format the roles the member had
        roles = [r.mention for r in member.roles if r.id != member.guild.default_role.id]
        roles_str = " ".join(roles) if roles else "None"

        # Prepare description lines
        description = [f"{member.mention} has left the server."]
        if member.joined_at:
            description.append(
                f"**Joined:** {discord.utils.format_dt(member.joined_at, 'F')} \
({discord.utils.format_dt(member.joined_at, 'R')})",
            )

        description.append(f"**Roles:** {roles_str}")

        await self._log_event(member, title, color, description)


async def setup(bot: "KiwiBot") -> None:
    """Add the cog to the bot."""
    if not all([bot.config.guild_id, bot.config.join_leave_log_channel_id]):
        log.warning(
            "GUILD_ID or JOIN_LEAVE_LOG_CHANNEL_ID is not configured. JoinLeaveLogCog will not be loaded.",
        )
        return

    await bot.add_cog(
        JoinLeaveLogCog(
            bot,
            guild_id=bot.config.guild_id,
            channel_id=bot.config.join_leave_log_channel_id,
        ),
    )
