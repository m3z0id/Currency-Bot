import datetime
import logging
from typing import TYPE_CHECKING, Final, Literal

import discord
from discord import app_commands
from discord.ext import commands

from modules.types import RoleId

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot


log = logging.getLogger(__name__)

# A dictionary to convert user-friendly time units to timedelta objects
TIME_UNITS: Final[dict[str, str]] = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


class DurationTransformer(app_commands.Transformer):
    """A transformer to convert a string like '10m' or '1d' into a timedelta."""

    async def transform(
        self,
        _interaction: discord.Interaction,
        value: str,
    ) -> datetime.timedelta:
        """Do the conversion."""
        value = value.lower().strip()
        unit = value[-1]

        if unit not in TIME_UNITS:
            # Using CommandInvokeError to provide a clean error message to the user.
            msg = "Invalid duration unit. Use 's', 'm', 'h', or 'd'."
            raise app_commands.CommandInvokeError(msg)

        try:
            time_value = int(value[:-1])
            delta = datetime.timedelta(**{TIME_UNITS[unit]: time_value})

            # Add Discord's 28-day timeout limit check directly in the transformer
            if delta > datetime.timedelta(days=28):
                msg = "Duration cannot exceed 28 days."
                raise app_commands.CommandInvokeError(msg)

        except ValueError as e:
            msg = "Invalid duration format. Example: `10m`, `2h`, `7d`"
            raise app_commands.CommandInvokeError(msg) from e
        else:
            return delta


class Moderate(commands.Cog):
    """A cog for moderation commands.

    Provides slash commands for banning, kicking, muting, and timing out members.
    """

    def __init__(self, bot: "KiwiBot", muted_role_id: RoleId | None) -> None:
        self.bot = bot
        self.muted_role_id = muted_role_id

    # Create a command group for all moderation actions
    moderate = app_commands.Group(
        name="moderate",
        description="Moderation commands for server management.",
        default_permissions=discord.Permissions(mute_members=True),
        guild_only=True,
    )

    # REFACTOR: Centralized check to handle common moderation validations.
    async def _pre_action_checks(self, interaction: discord.Interaction, member: discord.Member) -> bool:
        """Perform common checks before any moderation action.

        Returns True if all checks pass, False otherwise.
        """
        # Edge Case: Check if moderator is trying to act on themselves.
        if member.id == interaction.user.id:
            await interaction.response.send_message("You cannot perform this action on yourself.", ephemeral=True)
            return False

        # Edge Case: Check if moderator is trying to act on the bot.
        if member.id == self.bot.user.id:
            await interaction.response.send_message("You cannot perform this action on me.", ephemeral=True)
            return False

        # Edge Case: Check if moderator is trying to act on the guild owner.
        if member.id == interaction.guild.owner_id:
            await interaction.response.send_message(
                "You cannot perform moderation actions on the server owner.",
                ephemeral=True,
            )
            return False

        # Role Hierarchy Check: Ensure moderator's role is higher than the target's.
        if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "You cannot moderate a member with an equal or higher role.",
                ephemeral=True,
            )
            return False

        # Bot Hierarchy Check: Ensure the bot's role is higher than the target's.
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "I cannot perform this action because my highest role is lower than the target member's role.",
                ephemeral=True,
            )
            return False

        return True

    async def _notify_member(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        action: str,
        reason: str | None,
        duration: str | None = None,
    ) -> None:
        """Send a DM to the member about the moderation action."""
        embed = discord.Embed(
            title=f"You have been {action} in {interaction.guild.name}",
            color=discord.Colour.red(),
        )
        embed.add_field(name="Reason", value=reason or "No reason provided.", inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=False)
        embed.set_footer(text=f"Moderator: {interaction.user.display_name}")

        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            log.warning(
                "Failed to DM %s (%s) - they may have DMs disabled.",
                member.display_name,
                member.id,
            )
        except discord.HTTPException:
            log.exception(
                "Failed to DM %s (%s) due to an HTTP error.",
                member.display_name,
                member.id,
            )

    # --- MODERATION COMMANDS ---

    @moderate.command(name="ban", description="Bans a member from the server.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
        delete_messages: Literal["Don't delete any", "Last 24 hours", "Last 7 days"] = "Don't delete any",
        notify_member: bool = True,
    ) -> None:
        """Bans a member and optionally deletes their recent messages."""
        if not await self._pre_action_checks(interaction, member):
            return

        delete_seconds = 0
        if delete_messages == "Last 24 hours":
            delete_seconds = 86400
        elif delete_messages == "Last 7 days":
            delete_seconds = 604800

        if notify_member:
            await self._notify_member(interaction, member, "banned", reason)

        try:
            await member.ban(reason=reason, delete_message_seconds=delete_seconds)
            await interaction.response.send_message(f"✅ **{member.display_name}** has been banned.", ephemeral=True)
            log.info("%s banned %s for: %s", interaction.user, member, reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have the required permissions to ban this member.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
            log.exception("Failed to ban %s", member)

    @moderate.command(name="kick", description="Kicks a member from the server.")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
        notify_member: bool = True,
    ) -> None:
        """Kicks a member from the server."""
        if not await self._pre_action_checks(interaction, member):
            return

        if notify_member:
            await self._notify_member(interaction, member, "kicked", reason)

        try:
            await member.kick(reason=reason)
            await interaction.response.send_message(f"✅ **{member.display_name}** has been kicked.", ephemeral=True)
            log.info("%s kicked %s for: %s", interaction.user, member, reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have the required permissions to kick this member.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
            log.exception("Failed to kick %s", member)

    @moderate.command(name="timeout", description="Times out a member for a specified duration.")
    @app_commands.describe(duration="Duration of the timeout (e.g., 10m, 1h, 3d). Max 28 days.")
    @app_commands.checks.has_permissions(mute_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: app_commands.Transform[datetime.timedelta, DurationTransformer],
        reason: str | None = None,
        notify_member: bool = True,
    ) -> None:
        """Time out a member for a given duration."""
        if not await self._pre_action_checks(interaction, member):
            return

        # The 'duration' is now already a validated timedelta object!
        end_timestamp = discord.utils.utcnow() + duration

        if notify_member:
            await self._notify_member(
                interaction,
                member,
                "timed out",
                reason,
                duration=f"until {discord.utils.format_dt(end_timestamp, 'F')}",
            )

        try:
            await member.timeout(duration, reason=reason)
            await interaction.response.send_message(
                f"✅ **{member.display_name}** has been timed out until {discord.utils.format_dt(end_timestamp, 'F')}.",
                ephemeral=True,
            )
            log.info(
                "%s timed out %s for %s. Reason: %s",
                interaction.user,
                member,
                str(duration),
                reason,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have the required permissions to timeout this member.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
            log.exception("Failed to timeout %s", member)

    # NEW: Command to remove a timeout.
    @moderate.command(name="untimeout", description="Removes a timeout from a member.")
    @app_commands.checks.has_permissions(mute_members=True)
    async def untimeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
        notify_member: bool = True,
    ) -> None:
        """Remove a timeout from a member."""
        if not await self._pre_action_checks(interaction, member):
            return

        if not member.is_timed_out():
            await interaction.response.send_message("This member is not currently timed out.", ephemeral=True)
            return

        if notify_member:
            await self._notify_member(interaction, member, "timeout removed", reason)

        try:
            await member.timeout(None, reason=reason)
            await interaction.response.send_message(
                f"✅ The timeout for **{member.display_name}** has been removed.",
                ephemeral=True,
            )
            log.info(
                "%s removed timeout from %s. Reason: %s",
                interaction.user,
                member,
                reason,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have the required permissions to remove this timeout.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
            log.exception("Failed to untimeout %s", member)

    @moderate.command(name="mute", description="Mutes a member by assigning the muted role.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
        notify_member: bool = True,
    ) -> None:
        """Mutes a member by adding a 'Muted' role."""
        if not await self._pre_action_checks(interaction, member):
            return

        if not self.muted_role_id:
            await interaction.response.send_message(
                "The Muted Role ID has not been configured by the bot owner.",
                ephemeral=True,
            )
            return

        muted_role = await interaction.guild.fetch_role(self.muted_role_id)
        if not muted_role:
            await interaction.response.send_message(
                "The configured muted role could not be found on this server. It may have been deleted.",
                ephemeral=True,
            )
            return

        if muted_role in member.roles:
            await interaction.response.send_message("This member is already muted.", ephemeral=True)
            return

        if notify_member:
            await self._notify_member(interaction, member, "muted", reason)

        try:
            await member.add_roles(muted_role, reason=reason)
            await interaction.response.send_message(f"✅ **{member.display_name}** has been muted.", ephemeral=True)
            log.info("%s muted %s. Reason: %s", interaction.user, member, reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have the permissions to assign the muted role.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
            log.exception("Failed to mute %s", member)

    @moderate.command(name="unmute", description="Unmutes a member by removing the muted role.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def unmute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
        notify_member: bool = True,
    ) -> None:
        """Unmutes a member by removing the 'Muted' role."""
        # Add hierarchy check to prevent lower-ranked moderators from unmuting higher-ranked members
        if not await self._pre_action_checks(interaction, member):
            return

        if not self.muted_role_id:
            await interaction.response.send_message(
                "The Muted Role ID has not been configured by the bot owner.",
                ephemeral=True,
            )
            return

        muted_role = await interaction.guild.fetch_role(self.muted_role_id)
        if not muted_role:
            await interaction.response.send_message(
                "The configured muted role could not be found on this server. It may have been deleted.",
                ephemeral=True,
            )
            return

        if muted_role not in member.roles:
            await interaction.response.send_message("This member is not currently muted.", ephemeral=True)
            return

        if notify_member:
            await self._notify_member(interaction, member, "unmuted", reason)

        try:
            await member.remove_roles(muted_role, reason=reason)
            await interaction.response.send_message(f"✅ **{member.display_name}** has been unmuted.", ephemeral=True)
            log.info("%s unmuted %s. Reason: %s", interaction.user, member, reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have the permissions to remove the muted role.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
            log.exception("Failed to unmute %s", member)


async def setup(bot: "KiwiBot") -> None:
    """Add the cog to the bot."""
    await bot.add_cog(Moderate(bot, muted_role_id=bot.config.muted_role_id))
