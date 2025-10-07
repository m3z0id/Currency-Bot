# In cogs/invites.py
import logging
import os
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from modules.CurrencyBot import CurrencyBot

log = logging.getLogger(__name__)

# --- Environment Configuration ---
try:
    GUILD_ID = int(os.environ["GUILD_ID"])
    ALERT_CHANNEL_ID = int(os.environ["JOIN_LEAVE_LOG_CHANNEL_ID"])
except (KeyError, ValueError):
    log.exception(
        "Missing or invalid GUILD_ID or ALERT_CHANNEL_ID environment variable",
    )
    GUILD_ID = None
    ALERT_CHANNEL_ID = None


class InvitesCog(commands.Cog):
    """A cog for tracking and displaying invite information."""

    # 1. Define the parent group for all invite commands
    invites = app_commands.Group(name="invites", description="Commands for invite tracking.")

    def __init__(self, bot: CurrencyBot) -> None:
        self.bot = bot
        self.invites: dict[str, int] = {}
        if not all([GUILD_ID, ALERT_CHANNEL_ID]):
            log.warning("InvitesCog will not function without GUILD_ID and ALERT_CHANNEL_ID.")
        else:
            self.bot.loop.create_task(self.cache_invites())

    async def cache_invites(self) -> None:
        """Cache the guild's invites on startup."""
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            log.error("Could not find guild with ID %s for invite caching.", GUILD_ID)
            return

        try:
            # Store invites with the code as the key and the uses as the value
            self.invites = {invite.code: invite.uses for invite in await guild.invites()}
            log.info(
                "Successfully cached %s invites for guild %s.",
                len(self.invites),
                guild.name,
            )
        except discord.Forbidden:
            log.exception(
                "Bot lacks 'Manage Server' permissions to fetch invites for guild %s.",
                guild.name,
            )
        except discord.HTTPException:
            log.exception(
                "An HTTP error occurred while fetching invites for guild %s.",
                guild.name,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Handle new members joining the server and finds the inviter by diffing invite uses."""
        if member.guild.id != GUILD_ID or member.bot:
            return

        alert_channel = self.bot.get_channel(ALERT_CHANNEL_ID)
        if not alert_channel or not isinstance(alert_channel, discord.TextChannel):
            log.warning("Could not find alert channel %s for invite tracking.", ALERT_CHANNEL_ID)
            return

        inviter = None
        found_invite = None
        try:
            current_invites = await member.guild.invites()
            # Compare current invites with the cached invites to find the one that was used
            for invite in current_invites:
                # If the invite is new or its use count has increased, it's the one we're looking for
                if invite.code not in self.invites or invite.uses > self.invites.get(invite.code, 0):
                    found_invite = invite
                    inviter = invite.inviter
                    break

            # Update the cache with the new uses
            self.invites = {invite.code: invite.uses for invite in current_invites}

        except discord.Forbidden:
            log.warning("Missing 'Manage Server' permissions to read invites for tracking.")
            await alert_channel.send(f"⚠️ I don't have permission to view server invites to track who invited {member.mention}.")
            return
        except discord.HTTPException:
            log.exception("HTTP error fetching invites.")
            await alert_channel.send(f"⚠️ An API error occurred while trying to find the inviter for {member.mention}.")
            return

        if not inviter:
            log.warning("Could not determine inviter for %s via invite usage.", member.name)
            await alert_channel.send(f"⚠️ Could not automatically determine the inviter for {member.mention}.")
            return

        is_new_invite = await self.bot.invites_db.insert_invite(member.id, str(inviter.id), member.guild.id)

        result_color = discord.Color.blue() if is_new_invite else discord.Color.orange()
        result_title = "✅ New Invite Recorded" if is_new_invite else "Welcome Back!"
        description = f"{member.mention} was invited by {inviter.mention}"

        embed = discord.Embed(
            title=result_title,
            description=description,
            color=result_color,
            timestamp=member.joined_at,
        )
        embed.set_author(name=f"{member.name} ({member.id})", icon_url=member.display_avatar)
        embed.set_footer(text=f"Invite code: {found_invite.code} ({found_invite.uses} uses)")
        await alert_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        """Handle new invite creation to keep the cache updated."""
        if invite.guild and invite.guild.id == GUILD_ID:
            self.invites[invite.code] = invite.uses
            log.info("Cached new invite '%s' for guild '%s'.", invite.code, invite.guild.name)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        """Handle invite deletion to keep the cache updated."""
        if invite.guild and invite.guild.id == GUILD_ID and invite.code in self.invites:
            del self.invites[invite.code]
            log.info("Removed deleted invite '%s' from cache for guild '%s'.", invite.code, invite.guild.name)

    # 2. Convert commands to subcommands of the 'invites' group
    @invites.command(name="top", description="Shows the invite leaderboard.")
    async def invites_top(self, interaction: discord.Interaction) -> None:
        """Display the top 10 inviters in an embed."""
        await interaction.response.defer()
        mapping = await self.bot.invites_db.get_invites_by_inviter(interaction.guild.id)

        embed = discord.Embed(title="🏆 Top Invites Leaderboard", color=discord.Color.gold())

        if not mapping:
            embed.description = "No invites have been tracked yet."
            await interaction.followup.send(embed=embed)
            return

        sorted_inviters = sorted(mapping.items(), key=lambda i: len(i[1]), reverse=True)

        leaderboard_text = ""
        emojis = ["🥇", "🥈", "🥉"]
        for i, (user_id, invited_list) in enumerate(sorted_inviters[:10]):
            rank = emojis[i] if i < len(emojis) else f"**#{i + 1}**"
            leaderboard_text += f"{rank} <@{user_id}> — **{len(invited_list)}** invites\n"

        embed.description = leaderboard_text
        await interaction.followup.send(embed=embed)

    @invites.command(name="mylist", description="Shows who you have invited to the server.")
    async def invites_mylist(self, interaction: discord.Interaction) -> None:
        """Show a list of members invited by the user."""
        await interaction.response.defer(ephemeral=True)
        all_invites = await self.bot.invites_db.get_invites_by_inviter(interaction.guild.id)
        user_invites = all_invites.get(str(interaction.user.id), [])

        embed = discord.Embed(title="Your Invited Members", color=discord.Color.purple())

        if not user_invites:
            embed.description = "You haven't invited anyone yet."
        else:
            names = " ".join(f"<@{i}>" for i in user_invites)
            embed.description = f"You have invited **{len(user_invites)}** people:\n\n{names}"

        await interaction.followup.send(embed=embed)

    @invites.command(name="import", description="[Owner] Bulk import invites from the API.")
    @app_commands.checks.has_permissions(administrator=True)
    async def invites_import(self, interaction: discord.Interaction) -> None:
        """Fetch all guild members and imports new invitee-inviter relationships."""
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("Starting bulk import... this may take a while.")

        try:
            existing_invitees = await self.bot.invites_db.get_all_invitee_ids(interaction.guild.id)
            all_members = await self.bot.invites_db.get_all_guild_members_api(interaction.guild.id)
        except Exception as e:
            log.exception("Error during bulk import preparation.")
            await interaction.followup.send(f"An error occurred during preparation: {e}")
            return

        if not all_members:
            await interaction.followup.send("Could not fetch any members from the Discord API.")
            return

        new_imports = 0
        for member_data in all_members:
            inviter_id = member_data.get("inviter_id")
            if not inviter_id:
                continue

            try:
                member_info = member_data["member"]
                invitee_id = int(member_info["user"]["id"])
                joined_at = member_info.get("joined_at")
            except (KeyError, ValueError):
                continue

            if joined_at:
                dt_object = datetime.fromisoformat(joined_at)
                joined_at = dt_object.strftime("%Y-%m-%d %H:%M:%S")

            if invitee_id in existing_invitees:
                continue

            if await self.bot.invites_db.insert_invite(invitee_id, inviter_id, interaction.guild.id, joined_at):
                new_imports += 1

        await interaction.followup.send(f"Import complete. Added {new_imports} new invite records.")


async def setup(bot: CurrencyBot) -> None:
    """Entry point for loading the cog."""
    if not all([GUILD_ID, ALERT_CHANNEL_ID]):
        log.error("InvitesCog not loaded due to missing environment variables.")
        return
    await bot.add_cog(InvitesCog(bot))
