import asyncio
import datetime
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from modules.discord_utils import ping_online_role
from modules.enums import StatName
from modules.types import GuildId, RoleId, UserId

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot
    from modules.UserDB import UserDB

log = logging.getLogger(__name__)

# --- Constants ---
BUMP_REMINDER_DELAY = datetime.timedelta(hours=2)
BACKUP_REMINDER_DELAY = datetime.timedelta(minutes=10)


class BumpHandlerCog(commands.Cog):
    """Handle rewards and reminders for server bumps from bots like Disboard."""

    def __init__(
        self,
        bot: "KiwiBot",
        disboard_bot_id: UserId,
        guild_id: GuildId,
        bumper_role_id: RoleId,
        backup_bumper_role_id: RoleId | None,
    ) -> None:
        self.bot = bot
        self.user_db: UserDB = bot.user_db
        self.reminder_task: asyncio.Task | None = None

        self.disboard_bot_id = disboard_bot_id
        self.guild_id = guild_id
        self.bumper_role_id = bumper_role_id
        self.backup_bumper_role_id = backup_bumper_role_id

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
        if message.guild and message.guild.id == self.guild_id and self._is_successful_bump_message(message):
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
                reward = random.randint(50, 80)
                user_id = UserId(bumper.id)
                guild_id = GuildId(message.guild.id)
                # Reward Currency
                await self.bot.stats_db.increment_stat(user_id, guild_id, StatName.CURRENCY, reward)
                new_bump_count = await self.bot.stats_db.increment_stat(user_id, guild_id, StatName.BUMPS, 1)
                log.info("Rewarded %s with $%d for bumping.", bumper.display_name, reward)
                await channel.send(
                    f"🎉 Thanks for your **{new_bump_count:,}th** bump, {bumper.mention}! You've received **${reward}**.",
                )

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
            await self._send_reminder_message(channel, last_bumper, self.bumper_role_id)
            return

        log.info("Scheduling bump reminder in %.2f seconds.", delay_seconds)

        # Calculate the delay for the backup reminder. If the primary reminder is already late,
        # this will be negative, and we'll adjust accordingly.
        backup_delay_seconds = delay_seconds + BACKUP_REMINDER_DELAY.total_seconds()

        async def reminder_coro() -> None:
            # Stage 1: Primary Reminder
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            await self._send_reminder_message(
                channel,
                last_bumper,
                self.bumper_role_id,
                is_backup=False,
            )
            # Stage 2: Backup Reminder (if configured)
            if self.backup_bumper_role_id:
                # Calculate how long to wait from *now* until the backup is due.
                # If the backup time is already in the past, this will be <= 0.
                remaining_backup_wait = backup_delay_seconds - delay_seconds
                if remaining_backup_wait > 0:
                    await asyncio.sleep(remaining_backup_wait)
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
        role_id: RoleId,
        *,
        is_backup: bool = False,
    ) -> None:
        """Construct and send the bump reminder message."""
        log.info(
            "Sending %s bump reminder to #%s.",
            "backup" if is_backup else "primary",
            channel.name,
        )
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
            log.exception(
                "Failed to send %s reminder to %s.",
                "backup" if is_backup else "primary",
                channel.name,
            )

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


async def setup(bot: "KiwiBot") -> None:
    """Load the cog."""
    if not all([bot.config.disboard_bot_id, bot.config.guild_id, bot.config.bumper_role_id]):
        log.error("BumpHandlerCog not loaded: One or more required IDs are not configured.")
        return

    await bot.add_cog(
        BumpHandlerCog(
            bot,
            disboard_bot_id=bot.config.disboard_bot_id,
            guild_id=bot.config.guild_id,
            bumper_role_id=bot.config.bumper_role_id,
            backup_bumper_role_id=bot.config.backup_bumper_role_id,
        ),
    )
