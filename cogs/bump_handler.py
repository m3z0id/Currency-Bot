import asyncio
import datetime
import logging
import os
import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from modules.discord_utils import ping_online_role
from modules.enums import StatName

if TYPE_CHECKING:
    from modules.CurrencyBot import CurrencyBot
    from modules.UserDB import UserDB

log = logging.getLogger(__name__)

# --- Constants ---
BUMP_REMINDER_DELAY = datetime.timedelta(hours=2)
BACKUP_REMINDER_DELAY = datetime.timedelta(minutes=10)


class BumpHandlerCog(commands.Cog):
    """Handle rewards and reminders for server bumps from bots like Disboard."""

    def __init__(self, bot: "CurrencyBot") -> None:
        self.bot = bot
        self.user_db: UserDB = bot.user_db
        self.reminder_task: asyncio.Task | None = None

        self.disboard_bot_id = int(os.getenv("DISBOARD_BOT_ID"))
        self.guild_id = int(os.getenv("GUILD_ID"))
        self.bumper_role_id = int(os.getenv("BUMPER_ROLE_ID"))
        backup_bumper_role_id_str = os.getenv("BACKUP_BUMPER_ROLE_ID")
        self.backup_bumper_role_id: int | None = int(backup_bumper_role_id_str) if backup_bumper_role_id_str else None

    async def cog_load(self) -> None:
        """On cog load, find the last bump and process it to schedule a reminder."""
        log.info("BumpHandlerCog loaded. Searching for the last bump...")
        guild = await self.bot.fetch_guild(self.guild_id)
        if not guild:
            log.error("Could not find guild %d for reminder scheduling.", self.guild_id)
            return

        last_bump_message = await self._find_last_bump_message(guild)
        if last_bump_message:
            log.info("Found historical bump message %s. Processing it.", last_bump_message.id)
            # Process the found message, but don't re-reward the user.
            await self._process_bump(last_bump_message, is_new_bump=False)
        else:
            log.info("No recent bump message found. No reminder scheduled.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for new messages to detect and process a successful bump."""
        if message.guild and self._is_successful_bump_message(message):
            log.info("Detected a new bump in message %s. Processing it.", message.id)
            await self._process_bump(message, is_new_bump=True)

    async def _process_bump(self, message: discord.Message, *, is_new_bump: bool) -> None:
        """Unified method to handle all bump logic.

        Args:
        ----
            message: The successful bump message from Disboard.
            is_new_bump: If True, grants a reward. If False, only schedules a reminder.

        """
        if not message.interaction_metadata:
            log.warning("Bump message %s has no interaction metadata.", message.id)
            return

        bumper = message.interaction_metadata.user
        channel = message.channel

        try:
            if is_new_bump:
                reward = random.randint(50, 100)
                # Reward Currency
                await self.bot.stats_db.increment_stat(bumper.id, StatName.CURRENCY, reward)
                await self.bot.stats_db.increment_stat(bumper.id, StatName.BUMPS, 1)
                log.info("Rewarded %s with $%d for bumping.", bumper.display_name, reward)
                await channel.send(f"🎉 Thanks for bumping, {bumper.mention}! You've received **${reward}**.")

            # --- Unified Reminder Scheduling ---
            if is_new_bump:
                delay_seconds = BUMP_REMINDER_DELAY.total_seconds()
            else:
                # Calculate remaining time for a historical bump
                time_since_bump = discord.utils.utcnow() - message.created_at
                remaining_delay = BUMP_REMINDER_DELAY - time_since_bump
                delay_seconds = remaining_delay.total_seconds()

            await self._schedule_reminder(channel, bumper.mention, delay_seconds)

        except (discord.HTTPException, discord.Forbidden):
            log.exception("Error processing bump message %s.", message.id)

    async def _schedule_reminder(
        self,
        channel: discord.TextChannel,
        last_bumper: str,
        delay_seconds: float,
    ) -> None:
        """Schedules or reschedules the bump reminder task."""
        if self.reminder_task and not self.reminder_task.done():
            self.reminder_task.cancel()

        if delay_seconds <= 0:
            log.info("Reminder delay is zero or negative, sending now.")
            await self._send_reminder_message(channel, last_bumper)
            return

        log.info("Scheduling bump reminder in %.2f seconds.", delay_seconds)

        async def reminder_coro() -> None:
            await asyncio.sleep(delay_seconds)

            # Stage 1: Primary Reminder
            await self._send_reminder_message(
                channel,
                last_bumper,
                self.bumper_role_id,
                is_backup=False,
            )

            # Stage 2: Backup Reminder (if configured)
            if self.backup_bumper_role_id:
                await asyncio.sleep(BACKUP_REMINDER_DELAY.total_seconds())
                await self._send_reminder_message(
                    channel,
                    last_bumper,
                    self.backup_bumper_role_id,
                    is_backup=True,
                )

        self.reminder_task = asyncio.create_task(reminder_coro())

    async def _send_reminder_message(
        self,
        channel: discord.TextChannel,
        last_bumper_mention: str,
        role_id: int,
        *,
        is_backup: bool = False,
    ) -> None:
        """Construct and send the bump reminder message."""
        log.info("Sending %s bump reminder to #%s.", "backup" if is_backup else "primary", channel.name)
        try:
            if is_backup:
                title = "⚠️ Still Need a Bump! ⚠️"
                prefix = "It's been a while! Can a backup bumper help out?"
                color = discord.Colour.orange()
            else:
                title = "⏰ Time to Bump! ⏰"
                prefix = "It's time to bump the server again!"
                color = discord.Colour.blue()

            description = f"{prefix} Use `/bump`.\n*Thanks to {last_bumper_mention} for the last one!*"
            reminder_embed = discord.Embed(title=title, description=description, color=color)
            role_to_ping = await channel.guild.fetch_role(role_id)
            ping_text = await ping_online_role(role_to_ping, self.user_db) if role_to_ping else ""

            await channel.send(content=ping_text, embed=reminder_embed)
        except (discord.HTTPException, discord.Forbidden):
            log.exception("Failed to send %s reminder to %s.", "backup" if is_backup else "primary", channel.name)

    async def _find_last_bump_message(self, guild: discord.Guild) -> discord.Message | None:
        """Scan channels to find the last successful bump message."""
        # fetch_channels because cache isn't yet populated
        candidate_channels = [c for c in (await guild.fetch_channels()) if "bump" in c.name.lower()]
        latest_bump_message: discord.Message | None = None

        for channel in candidate_channels:
            try:
                async for message in channel.history(limit=50):
                    if self._is_successful_bump_message(message):
                        if not latest_bump_message or message.created_at > latest_bump_message.created_at:
                            latest_bump_message = message
                        # Since history is newest-first, we can stop after finding the first one.
                        break
            except (discord.Forbidden, discord.HTTPException):
                continue
        return latest_bump_message

    def _is_successful_bump_message(self, message: discord.Message) -> bool:
        """Check if a message is a successful Disboard bump."""
        return (
            message.author.id == self.disboard_bot_id
            and message.embeds
            and message.embeds[0].description
            and "Bump done!" in message.embeds[0].description
        )


async def setup(bot: "CurrencyBot") -> None:
    """Load the cog."""
    if not os.getenv("DISBOARD_BOT_ID"):
        log.error("BumpHandlerCog not loaded: DISBOARD_BOT_ID is not configured.")
        return
    await bot.add_cog(BumpHandlerCog(bot))
