import asyncio
import datetime
import logging
import typing

import discord
from discord.ext import commands

from modules.types import ChannelId, GuildId, RoleId

if typing.TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)


class ModLogCog(commands.Cog):
    """A cog for logging moderation actions to a specified channel."""

    def __init__(self, bot: "KiwiBot", guild_id: GuildId, mod_channel_id: ChannelId, muted_role_id: RoleId | None) -> None:
        self.bot = bot
        self.privileged_guild_id = guild_id
        self.mod_channel_id = mod_channel_id
        self.muted_role_id = muted_role_id
        self.mod_channel: discord.TextChannel | None = None

    async def cog_load(self) -> None:
        """Fetch the channel object when the cog is loaded."""
        if not self.bot.get_guild(self.privileged_guild_id):
            return
        channel = await self.bot.fetch_channel(self.mod_channel_id)
        if isinstance(channel, discord.TextChannel):
            self.mod_channel = channel
            log.info("Moderation logging channel set to #%s", self.mod_channel.name)
        else:
            log.error(
                "Could not find the MOD_CHANNEL_ID channel or it is not a text channel.",
            )

    async def _log_action(
        self,
        *,
        title: str,
        color: discord.Colour,
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
            timestamp=discord.utils.utcnow(),
        )

        name = f"{member.name} ({member.display_name})"
        embed.set_author(name=name, icon_url=member.display_avatar)

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
        THRESHOLD = 10
        after = discord.utils.utcnow() - datetime.timedelta(seconds=THRESHOLD)
        try:
            async for entry in guild.audit_logs(action=action, after=after):
                # Check if the entry is recent
                if entry.target and entry.target.id == target.id:
                    return entry.user, entry.reason
        except discord.Forbidden:
            log.warning("Missing 'View Audit Log' permissions to identify moderator.")
        except discord.HTTPException:
            log.exception("Failed to fetch audit logs")

        return None, None

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ) -> None:
        if guild.id != self.privileged_guild_id:
            return

        moderator, reason = await self._fetch_audit_entry(
            guild,
            user,
            discord.AuditLogAction.ban,
        )
        await self._log_action(
            title="Member Banned",
            color=discord.Colour.red(),
            member=user,
            moderator=moderator,
            reason=reason,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        if guild.id != self.privileged_guild_id:
            return

        moderator, reason = await self._fetch_audit_entry(
            guild,
            user,
            discord.AuditLogAction.unban,
        )
        await self._log_action(
            title="Member Unbanned",
            color=discord.Colour.green(),
            member=user,
            moderator=moderator,
            reason=reason,
            include_reason=False,  # Reason field is not shown
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self.privileged_guild_id:
            return

        moderator, reason = await self._fetch_audit_entry(
            member.guild,
            member,
            discord.AuditLogAction.kick,
        )
        # If a kick entry is found, it was a kick. Otherwise, it was a leave.
        if moderator:
            await self._log_action(
                title="Member Kicked",
                color=discord.Colour.orange(),
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
        if before.guild.id != self.privileged_guild_id:
            return

        if before.timed_out_until != after.timed_out_until:
            # Member Timed Out
            if not before.timed_out_until and after.timed_out_until:
                moderator, reason = await self._fetch_audit_entry(
                    after.guild,
                    after,
                    discord.AuditLogAction.member_update,
                )
                duration_str = f"{discord.utils.format_dt(after.timed_out_until, 'F')} \
({discord.utils.format_dt(after.timed_out_until, 'R')})"

                await self._log_action(
                    title="Member Timed Out",
                    color=discord.Colour.gold(),
                    member=after,
                    moderator=moderator,
                    reason=reason,
                    duration=duration_str,
                )
            # Timeout Removed
            elif before.timed_out_until and not after.timed_out_until:
                moderator, reason = await self._fetch_audit_entry(
                    after.guild,
                    after,
                    discord.AuditLogAction.member_update,
                )
                await self._log_action(
                    title="Timeout Removed",
                    color=discord.Colour.blue(),
                    member=after,
                    moderator=moderator,
                    reason=reason,
                    include_reason=False,
                )

        # --- Muted Role Tracking ---
        if self.muted_role_id and before.roles != after.roles:
            muted_role = after.guild.get_role(self.muted_role_id)
            if not muted_role:
                return

            # Role added
            if muted_role not in before.roles and muted_role in after.roles:
                moderator, reason = await self._fetch_audit_entry(
                    after.guild,
                    after,
                    discord.AuditLogAction.member_role_update,
                )
                await self._log_action(
                    title="Member Muted",
                    color=discord.Colour.dark_orange(),
                    member=after,
                    moderator=moderator,
                    reason=reason,
                )
            # Role removed
            elif muted_role in before.roles and muted_role not in after.roles:
                moderator, reason = await self._fetch_audit_entry(
                    after.guild,
                    after,
                    discord.AuditLogAction.member_role_update,
                )
                await self._log_action(
                    title="Member Unmuted",
                    color=discord.Colour.teal(),
                    member=after,
                    moderator=moderator,
                    reason=reason,
                    include_reason=False,
                )


async def setup(bot: "KiwiBot") -> None:
    """Add the cog to the bot."""
    if not all([bot.config.guild_id, bot.config.mod_channel_id]):
        log.warning("GUILD_ID or MOD_CHANNEL_ID is not configured. ModLog cog will not be loaded.")
        return

    await bot.add_cog(
        ModLogCog(
            bot,
            guild_id=bot.config.guild_id,
            mod_channel_id=bot.config.mod_channel_id,
            muted_role_id=bot.config.muted_role_id,
        ),
    )
