import asyncio
import datetime
import logging
import typing

import discord
from discord.ext import commands

from modules.types import GuildId

if typing.TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)


class ModLogCog(commands.Cog):
    """A cog for logging moderation actions to a specified channel."""

    def __init__(self, bot: "KiwiBot") -> None:
        self.bot = bot

    async def _log_action(
        self,
        *,
        title: str,
        color: discord.Colour,
        member: discord.User | discord.Member,
        moderator: discord.User | None,
        reason: str | None,
        duration: str | None = None,
        guild_id: GuildId | None = None,  # Added guild_id to fetch config dynamically
        include_reason: bool = True,
    ) -> None:
        """Create and send the log embed."""
        if not guild_id:
            log.warning("Cannot log action without a guild ID.")
            return
        config = await self.bot.config_db.get_guild_config(guild_id)
        mod_channel_id = config.mod_log_channel_id
        if not mod_channel_id:
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

        mod_channel = self.bot.get_channel(mod_channel_id)
        if not isinstance(mod_channel, discord.TextChannel):
            log.warning(
                "Configured mod log channel %d not found or is not a text channel for guild %d.",
                mod_channel_id,
                guild_id,
            )
            await self.bot.log_admin_warning(
                guild_id=guild_id,
                warning_type="log_channel_missing",
                description=(
                    f"The `Moderation Log` channel (`{mod_channel_id}`) could not be found. "
                    "It may have been deleted. Moderation actions will not be logged."
                ),
                level="ERROR",
            )
            return

        try:
            # Defensively disable all pings. Only display mentions.
            await mod_channel.send(embed=embed, allowed_mentions=None)
        except (discord.Forbidden, discord.HTTPException):
            # Log the exception but don't re-raise, as logging should not block other operations.
            log.exception("Failed to send log message to mod channel")
            await self.bot.log_admin_warning(
                guild_id=guild_id,
                warning_type="log_channel_permission",
                description=(
                    f"I failed to send a message to the `Moderation Log` channel ({mod_channel.mention}). "
                    "Please check my `Send Messages` and `Embed Links` permissions in that channel."
                ),
                level="ERROR",
            )

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
        config = await self.bot.config_db.get_guild_config(GuildId(guild.id))
        mod_channel_id = config.mod_log_channel_id

        if not mod_channel_id:
            # This guild hasn't configured this feature, so we do nothing.
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
            guild_id=GuildId(guild.id),
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        config = await self.bot.config_db.get_guild_config(GuildId(guild.id))
        mod_channel_id = config.mod_log_channel_id

        if not mod_channel_id:
            # This guild hasn't configured this feature, so we do nothing.
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
            guild_id=GuildId(guild.id),
            include_reason=False,  # Reason field is not shown
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        config = await self.bot.config_db.get_guild_config(GuildId(member.guild.id))
        mod_channel_id = config.mod_log_channel_id

        if not mod_channel_id:
            return  # This guild hasn't configured this feature, so we do nothing.

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
                guild_id=GuildId(member.guild.id),
            )

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        config = await self.bot.config_db.get_guild_config(GuildId(before.guild.id))
        mod_channel_id = config.mod_log_channel_id

        if not mod_channel_id:
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
                    guild_id=GuildId(after.guild.id),
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
                    guild_id=GuildId(after.guild.id),
                    include_reason=False,
                )

        # --- Muted Role Tracking ---
        muted_role_id = config.muted_role_id
        if muted_role_id and before.roles != after.roles:
            muted_role = after.guild.get_role(muted_role_id)
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
                    guild_id=GuildId(after.guild.id),
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
                    guild_id=GuildId(after.guild.id),
                    include_reason=False,
                )


async def setup(bot: "KiwiBot") -> None:
    """Add the cog to the bot."""
    # ModLogCog is now stateless and will fetch config per guild.
    await bot.add_cog(ModLogCog(bot))
