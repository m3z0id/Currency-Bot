import asyncio
import logging
import string
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from modules.enums import StatName

# Import the refactored logic and helpers
from modules.leveling_utils import LevelBotProtocol, get_level, to_next_level

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot

log = logging.getLogger(__name__)

# UDP Server Configuration
UDP_HOST = "127.0.0.1"

# --- Constants ---
COOLDOWN_SECONDS = 5 * 60
LONG_ABSENCE_BONUS_HOURS = 6
lowercase_letters = set(string.ascii_lowercase)


class LeaderboardView(discord.ui.View):
    """A view for paginating through the server leaderboard."""

    def __init__(
        self,
        bot: "KiwiBot",
        data: list[tuple[int, int]],
        per_page: int = 10,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.data = data
        self.per_page = per_page
        self.current_page = 0
        self.max_page = (len(self.data) - 1) // self.per_page

    async def get_page_embed(self) -> discord.Embed:
        """Generate the embed for the current page."""
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_page

        start = self.current_page * self.per_page
        end = start + self.per_page
        page_data = self.data[start:end]

        embed = discord.Embed(
            title="🏆 Server Leaderboard",
            color=discord.Color.gold(),
        )

        description = []
        for i, (user_id, xp) in enumerate(page_data, start=start + 1):
            user = self.bot.get_user(user_id) or f"Unknown User ({user_id})"
            level = get_level(xp)
            description.append(f"`{i}.` **{user}** - Level {level} ({xp:,} XP)")

        if not description:
            description.append("The leaderboard is empty!")

        embed.description = "\n".join(description)
        embed.set_footer(text=f"Page {self.current_page + 1} / {self.max_page + 1}")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def previous_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.current_page < self.max_page:
            self.current_page += 1
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)


class LevelingCog(commands.Cog):
    """Handles the leveling system, including XP gain, ranks, and leaderboards."""

    # Define the parent group for all leveling commands
    level = app_commands.Group(name="level", description="Commands for the leveling system.")

    def __init__(
        self,
        bot: "KiwiBot",
        level_up_channel_id: int | None,
        guild_id: int | None,
        udp_port: int | None,
    ) -> None:
        self.bot = bot
        self.last_activity_timestamps: dict[int, float] = {}
        self.udp_transport: asyncio.DatagramTransport | None = None
        self.level_up_channel_id = level_up_channel_id
        self.guild_id = guild_id
        self.udp_port = udp_port

    async def cog_load(self) -> None:
        """Start the UDP server when the cog is loaded."""
        if not self.udp_port:
            return  # Do not start if the port is not configured.

        loop = asyncio.get_running_loop()
        try:
            self.udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: LevelBotProtocol(self),
                local_addr=(UDP_HOST, self.udp_port),
            )
            log.info("Leveling UDP server started on %s:%d.", UDP_HOST, self.udp_port)
        except OSError:
            log.exception("Failed to start leveling UDP server")

    async def cog_unload(self) -> None:
        """Stop the UDP server when the cog is unloaded."""
        if self.udp_transport:
            self.udp_transport.close()
            log.info("Leveling UDP server stopped.")

    def _get_addable_xp(self, user_id: int) -> int:
        """Determine if a user is eligible for XP based on cooldowns."""
        now = time.time()
        seconds_since_last = now - self.last_activity_timestamps.get(user_id, 0)

        if seconds_since_last > COOLDOWN_SECONDS:
            self.last_activity_timestamps[user_id] = now
            # Bonus for being away for more than 6 hours
            if seconds_since_last > (LONG_ABSENCE_BONUS_HOURS * 3600):
                return 4  # Bonus for long absence
            return 1
        return 0

    async def _handle_level_up_announcement(self, user_id: int, new_level: int, new_xp: int, source: str) -> None:
        """Format and send a level-up announcement to the configured channel."""
        if not self.level_up_channel_id:
            return

        channel = self.bot.get_channel(self.level_up_channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Level-up channel ID %s is invalid or not found.", self.level_up_channel_id)
            return

        user = self.bot.get_user(user_id)
        if not user:
            return

        source_text = "from in-game activity! 🚀" if source == "udp" else "from chatting! 💬"
        embed = discord.Embed(
            description=f"🎉 **{user.mention} has reached level {new_level}!**",
            color=discord.Color.blue(),
        ).set_author(name=f"Leveled up {source_text}", icon_url=user.display_avatar.url)

        # Restored Feature: Add the helpful footer back to the embed.
        xp_for_next = to_next_level(new_xp)
        embed.set_footer(text=f"You need {xp_for_next:,} more XP for the next level.")
        await channel.send(embed=embed)

    async def _grant_xp(self, user_id: int, source: str, amount: int) -> None:
        """Check eligibility and grant XP."""
        if await self.bot.user_db.is_user_opted_out(user_id):
            return

        # Single atomic call to the enhanced database method
        new_xp = await self.bot.stats_db.increment_stat(user_id, StatName.XP, amount)

        # Safely derive the old XP from the result
        old_xp = new_xp - amount

        old_level = get_level(old_xp)
        new_level = get_level(new_xp)

        if new_level > old_level:
            log.info("User %d leveled up to %d from %s.", user_id, new_level, source)
            # Pass new_xp to the handler so it can calculate the footer
            await self._handle_level_up_announcement(user_id, new_level, new_xp, source)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Grant XP to users for sending messages."""
        if message.author.bot or not message.guild:
            return

        # Short message filter from original implementation
        if len(set(message.clean_content.lower()).intersection(lowercase_letters)) <= 3:  # noqa: PLR2004
            return

        xp_to_add = self._get_addable_xp(message.author.id)
        if xp_to_add > 0:
            await self._grant_xp(message.author.id, "message", xp_to_add)

    async def grant_udp_xp(self, user_id: int) -> None:
        """Handle UDP-based XP for GUILD_ID."""
        if not self.guild_id:
            log.warning("Cannot grant UDP XP: GUILD_ID environment variable is not set.")
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild or not guild.get_member(user_id):
            # Don't grant XP if the user isn't in the specified server
            return

        xp_to_add = self._get_addable_xp(user_id)
        if xp_to_add > 0:
            await self._grant_xp(user_id, "udp", xp_to_add)

    @level.command(name="opt-out", description="Exclude yourself from the leveling system.")
    async def level_opt_out(self, interaction: discord.Interaction) -> None:
        """Allow a user to opt-out without resetting their XP."""
        if await self.bot.user_db.is_user_opted_out(interaction.user.id):
            await interaction.response.send_message("ℹ️ You are already opted out.", ephemeral=True)  # noqa: RUF001
            return

        await self.bot.user_db.set_leveling_opt_out(interaction.user.id, True)
        # The line resetting XP has been removed to fix the destructive behavior.
        await interaction.response.send_message(
            "✅ You are now excluded from the leveling system. Your XP is saved for when you return.",
            ephemeral=True,
        )

    @level.command(name="opt-in", description="Re-include yourself in the leveling system.")
    async def level_opt_in(self, interaction: discord.Interaction) -> None:
        """Allow a user to opt back into the leveling system."""
        if not await self.bot.user_db.is_user_opted_out(interaction.user.id):
            await interaction.response.send_message("ℹ️ You are already opted in.", ephemeral=True)  # noqa: RUF001
            return

        await self.bot.user_db.set_leveling_opt_out(interaction.user.id, False)
        await interaction.response.send_message("✅ Welcome back! You will now gain XP again.", ephemeral=True)

    @level.command(name="rank", description="Check your or another user's rank.")
    async def level_rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        """Display the level, XP, and rank of a user, with a progress bar."""
        target_user = member or interaction.user
        ephemeral = member is None or member.id == interaction.user.id

        if target_user.bot:
            await interaction.response.send_message("Bots do not participate in the leveling system.", ephemeral=True)
            return

        if await self.bot.user_db.is_user_opted_out(target_user.id):
            await interaction.response.send_message(
                f"ℹ️ {target_user.display_name} has opted out of the leveling system.",  # noqa: RUF001
                ephemeral=ephemeral,
            )
            return

        xp = await self.bot.stats_db.get_stat(target_user.id, StatName.XP)
        level = get_level(xp)
        xp_for_next = to_next_level(xp)

        # Progress bar calculation from original implementation
        current_level_xp_req = round(level**2.5 + 10)
        next_level_xp_req = round((level + 1) ** 2.5 + 10)
        progress_in_level = xp - current_level_xp_req
        total_for_level = next_level_xp_req - current_level_xp_req

        progress_bar = "■" * int((progress_in_level / total_for_level) * 10) + "□" * (
            10 - int((progress_in_level / total_for_level) * 10)
        )

        embed = discord.Embed(
            title=f"📊 Level Stats for {target_user.display_name}",
            color=discord.Color.random(),
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="Level", value=f"**{level}**", inline=True)
        embed.add_field(name="Total XP", value=f"**{xp:,}**", inline=True)
        embed.add_field(
            name=f"Progress to Level {level + 1}",
            value=f"`{progress_bar}`\n({progress_in_level:,} / {total_for_level:,} XP)",
            inline=False,
        )
        embed.set_footer(text=f"You need {xp_for_next:,} more XP for the next level.")
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @level.command(name="leaderboard", description="Shows the XP leaderboard.")
    async def level_leaderboard(self, interaction: discord.Interaction) -> None:
        """Show the XP leaderboard.."""
        await interaction.response.defer()

        # This now returns a pre-filtered list, fixing the N+1 query issue.
        data = await self.bot.stats_db.get_leaderboard(StatName.XP, limit=200)

        if not data:
            await interaction.followup.send("The leaderboard is currently empty.")
            return

        view = LeaderboardView(self.bot, data)
        embed = await view.get_page_embed()
        await interaction.followup.send(embed=embed, view=view)

    @level.command(name="reset-xp", description="[Admin] Resets a user's XP.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_reset_xp(self, interaction: discord.Interaction, member: discord.Member) -> None:
        # This command is simple and can be defined locally.
        view = discord.ui.View(timeout=30)

        async def confirm_callback(interaction: discord.Interaction) -> None:
            # First, get the user's current XP
            current_xp = await self.bot.stats_db.get_stat(member.id, StatName.XP)

            if current_xp > 0:
                # If they have XP, reset it and confirm
                await self.bot.stats_db.set_stat(member.id, StatName.XP, 0)
                await interaction.response.edit_message(
                    content=f"✅ Successfully reset all XP for **{member.display_name}**.",
                    view=None,
                )
            else:
                # If they had no XP, provide contextual feedback
                await interaction.response.edit_message(
                    content=f"ℹ️ **{member.display_name}** had no XP to reset.",  # noqa: RUF001
                    view=None,
                )

        async def cancel_callback(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(content="❌ Action cancelled.", view=None)

        confirm_button = discord.ui.Button(label="Confirm Reset", style=discord.ButtonStyle.danger)
        confirm_button.callback = confirm_callback

        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = cancel_callback

        view.add_item(confirm_button)
        view.add_item(cancel_button)

        await interaction.response.send_message(
            f"⚠️ Are you sure you want to permanently delete all XP for **{member.display_name}**?",
            view=view,
            ephemeral=True,
        )


async def setup(bot: "KiwiBot") -> None:
    """Add the LevelingCog to the bot."""
    await bot.add_cog(
        LevelingCog(
            bot,
            level_up_channel_id=bot.config.level_up_channel_id,
            guild_id=bot.config.guild_id,
            udp_port=bot.config.udp_port,
        ),
    )
