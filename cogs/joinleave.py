import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from modules.dtypes import GuildId
from modules.utils import format_ordinal

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)


class JoinLeaveLogCog(commands.Cog):
    """A cog for logging member join and leave events to a specified channel."""

    def __init__(self, bot: "KiwiBot") -> None:
        self.bot = bot

    async def _log_event(
        self,
        member: discord.Member,
        title: str,
        color: discord.Colour,
        description_parts: list[str],
    ) -> None:
        config = await self.bot.config_db.get_guild_config(GuildId(member.guild.id))
        log_channel_id = config.join_leave_log_channel_id

        if not log_channel_id:
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

        log_channel = self.bot.get_channel(log_channel_id)
        if not isinstance(log_channel, discord.TextChannel):
            log.warning(
                "Configured join/leave log channel %d not found or is not a text channel for guild %d.",
                log_channel_id,
                member.guild.id,
            )
            return

        try:
            # Defensively disable all pings. Only display mentions.
            await log_channel.send(embed=embed, allowed_mentions=None)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Failed to send message to join/leave log channel")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:  # noqa: PLR0912
        """Handle logging when a new member joins or rejoins the server."""
        if not member.bot:
            # Log properties that indicate a real, established user account
            # This helps identify potential self-bots (which would have none of these)
            user_indicators: list[str] = []
            if member.accent_colour:
                user_indicators.append(f"accent_colour={member.accent_colour}")
            if member.display_banner:
                user_indicators.append("has_display_banner=True")
            if member.avatar_decoration:
                user_indicators.append("has_avatar_decoration=True")
            if member.guild_avatar:
                user_indicators.append("has_guild_avatar=True")
            if member.premium_since:
                user_indicators.append(f"boosting_since={member.premium_since}")

            # PublicUserFlags
            public_flags = [flag_name for flag_name, has_flag in member.public_flags if has_flag]
            if public_flags:
                user_indicators.append(f"public_flags={','.join(public_flags)}")

            # MemberFlags
            if member.flags.completed_onboarding:
                user_indicators.append("completed_onboarding=True")
            if member.flags.bypasses_verification:
                user_indicators.append("bypasses_verification=True")

            log.info(
                "Member join: %s (%d). Indicators: %s",
                str(member),
                member.id,
                "; ".join(user_indicators) if user_indicators else "None",
            )

        config = await self.bot.config_db.get_guild_config(GuildId(member.guild.id))
        if not config.join_leave_log_channel_id:
            return  # This guild hasn't configured this feature, so we do nothing.

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
            f"{member.mention} was the {format_ordinal(member_count)} member to join.",
            f"Account created: {discord.utils.format_dt(member.created_at, 'F')} \
({discord.utils.format_dt(member.created_at, 'R')})",
        ]

        await self._log_event(member, title, color, description)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Handle logging when a member leaves the server."""
        config = await self.bot.config_db.get_guild_config(GuildId(member.guild.id))
        if not config.join_leave_log_channel_id:
            return  # This guild hasn't configured this feature, so we do nothing.

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
    # JoinLeaveLogCog is now stateless and will fetch config per guild.
    await bot.add_cog(JoinLeaveLogCog(bot))
