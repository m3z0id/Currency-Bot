import asyncio
import datetime
import logging
import os
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from modules.CurrencyBot import CurrencyBot

log = logging.getLogger(__name__)


class ModLogCog(commands.Cog):
    """A cog for logging moderation actions to a specified channel."""

    def __init__(self, bot: "CurrencyBot", mod_channel_id: int) -> None:
        self.bot = bot
        self.mod_channel_id = mod_channel_id
        self.mod_channel: discord.TextChannel | None = None

    async def cog_load(self) -> None:
        """Fetch the channel object when the cog is loaded."""
        channel = self.bot.get_channel(self.mod_channel_id)
        if isinstance(channel, discord.TextChannel):
            self.mod_channel = channel
            log.info("Moderation logging channel set to #%s", self.mod_channel.name)
        else:
            log.error(
                "Could not find the MOD_CHANNEL_ID channel or it is not a text channel.",
            )

    async def _log_action(  # noqa: PLR0913
        self,
        *,
        title: str,
        color: discord.Color,
        member: discord.User | discord.Member,
        moderator: discord.User | None,
        reason: str | None,
        duration: str | None = None,
        include_reason: bool = True,
    ) -> None:
        """Create and send the log embed."""
        if not self.mod_channel:
            return

        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.datetime.now(datetime.UTC),
        )

        name = f"{member.name} ({member.display_name})"
        embed.set_author(name=name, icon_url=member.display_avatar.url)

        description = f"**Target:** {member.mention} (`{member.id}`)"
        description += f"\n**Moderator:** {moderator.mention if moderator else 'Unknown'}"
        embed.description = description

        if include_reason:
            embed.add_field(
                name="Reason",
                value=reason if reason else "Not provided.",
                inline=False,
            )
        if duration:
            embed.add_field(name="Ends On", value=duration, inline=False)

        try:
            # Defensively disable all pings. Only display mentions.
            await self.mod_channel.send(embed=embed, allowed_mentions=None)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Failed to send log message to mod channel")

    async def _fetch_audit_entry(
        self,
        guild: discord.Guild,
        target: discord.User | discord.Member,
        action: discord.AuditLogAction,
    ) -> tuple[discord.User | None, str | None]:
        """Wait and fetch the moderator and reason from the audit log."""
        await asyncio.sleep(3)  # Wait for the audit log to populate
        moderator, reason = None, None
        THRESHOLD = 10
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                # Check if the entry is recent
                if (
                    entry.target
                    and entry.target.id == target.id
                    and (datetime.datetime.now(datetime.UTC) - entry.created_at).total_seconds() < THRESHOLD
                ):
                    moderator = entry.user
                    reason = entry.reason
                    break
        except discord.Forbidden:
            log.warning("Missing 'View Audit Log' permissions to identify moderator.")
        except discord.HTTPException:
            log.exception("Failed to fetch audit logs")

        return moderator, reason

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ) -> None:
        moderator, reason = await self._fetch_audit_entry(
            guild,
            user,
            discord.AuditLogAction.ban,
        )
        await self._log_action(
            title="Member Banned",
            color=discord.Color.red(),
            member=user,
            moderator=moderator,
            reason=reason,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        moderator, reason = await self._fetch_audit_entry(
            guild,
            user,
            discord.AuditLogAction.unban,
        )
        await self._log_action(
            title="Member Unbanned",
            color=discord.Color.green(),
            member=user,
            moderator=moderator,
            reason=reason,
            include_reason=False,  # Reason field is not shown
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        moderator, reason = await self._fetch_audit_entry(
            member.guild,
            member,
            discord.AuditLogAction.kick,
        )
        # If a kick entry is found, it was a kick. Otherwise, it was a leave.
        if moderator:
            await self._log_action(
                title="Member Kicked",
                color=discord.Color.orange(),
                member=member,
                moderator=moderator,
                reason=reason,
            )

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        if before.timed_out_until == after.timed_out_until:
            return  # No change in timeout status

        # Member Muted (Timeout Applied)
        if not before.timed_out_until and after.timed_out_until:
            moderator, reason = await self._fetch_audit_entry(
                after.guild,
                after,
                discord.AuditLogAction.member_update,
            )
            # Get the Unix timestamp for the end date
            timestamp = int(after.timed_out_until.timestamp())
            # Format using Discord's relative time syntax
            duration_str = f"<t:{timestamp}:F> (<t:{timestamp}:R>)"

            await self._log_action(
                title="Member Muted (Timeout)",
                color=discord.Color.gold(),
                member=after,
                moderator=moderator,
                reason=reason,
                duration=duration_str,
            )

        # Member Unmuted (Timeout Removed)
        elif before.timed_out_until and not after.timed_out_until:
            moderator, reason = await self._fetch_audit_entry(
                after.guild,
                after,
                discord.AuditLogAction.member_update,
            )
            await self._log_action(
                title="Member Unmuted (Timeout Removed)",
                color=discord.Color.blue(),
                member=after,
                moderator=moderator,
                reason=reason,
                include_reason=False,  # Reason field is not shown
            )


async def setup(bot: "CurrencyBot") -> None:
    """Add the cog to the bot."""
    mod_channel_id_str = os.getenv("MOD_CHANNEL_ID")
    if not mod_channel_id_str:
        log.warning(
            "MOD_CHANNEL_ID environment variable not set. ModLog cog will not be loaded.",
        )
        return

    try:
        mod_channel_id = int(mod_channel_id_str)
        await bot.add_cog(ModLogCog(bot, mod_channel_id))
    except ValueError:
        log.exception(
            "MOD_CHANNEL_ID is not a valid integer. ModLog cog will not be loaded.",
        )
