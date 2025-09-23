# cogs/bump_handler.py
import asyncio
import logging
import os
import random
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from modules.CurrencyBot import CurrencyBot
    from modules.CurrencyDB import CurrencyDB

log = logging.getLogger(__name__)

# --- Configuration ---
DISBOARD_BOT_ID = int(os.getenv("DISBOARD_BOT_ID"))

BUMP_SUCCESS_REGEX = re.compile(r"Bump done!.*<@(\d+)>")
BUMP_REMINDER_DELAY_SECONDS = 2 * 60 * 60  # 2 hours


class BumpHandlerCog(commands.Cog):
    """Handle rewards and reminders for server bumps from bots like Disboard."""

    def __init__(self, bot: "CurrencyBot") -> None:
        self.bot = bot
        self.currency_db: CurrencyDB = bot.currency_db

    async def _process_bump_message(self, message: discord.Message) -> None:
        """Shared logic to check a message for a successful bump."""
        # --- Rule 1: Initial Filtering ---
        # Ignore messages from anyone but the Disboard bot.
        if not message.guild or not message.author.bot:
            # if not message.guild or message.author.id != DISBOARD_BOT_ID:
            return

        # --- Rule 2: Check for Bump Success Embed ---
        # Use debug logging to see what the bot sees.
        log.debug(f"Processing message {message.id}. Embeds count: {len(message.embeds)}")
        if not message.embeds:
            log.debug(f"Message {message.id} has no embeds. Skipping.")
            return

        embed = message.embeds[0]
        if not embed.description:
            log.debug(f"Message {message.id} has an embed but no description. Skipping.")
            return

        # --- Rule 3: Parse Embed and Process Reward/Reminder ---
        if match := BUMP_SUCCESS_REGEX.search(embed.description):
            log.info(f"Detected a successful bump in message {message.id}.")
            try:
                bumper_id = int(match.group(1))
                # Use message.guild.get_member for efficiency if user is in cache,
                # otherwise fetch from API.
                bumper: discord.User | discord.Member | None = message.guild.get_member(bumper_id)
                if not bumper:
                    bumper = await self.bot.fetch_user(bumper_id)

                reward = random.randint(50, 100)

                await self.currency_db.add_money(bumper.id, reward)
                log.info("Rewarded %s with $%s for bumping.", bumper.display_name, reward)

                await message.channel.send(
                    f"🎉 Thanks for bumping, {bumper.mention}! You've received **${reward}**.",
                )

                log.info("Scheduling a 2-hour bump reminder for %s.", bumper.display_name)
                # Ensure the channel is a TextChannel before creating the task
                if isinstance(message.channel, discord.TextChannel):
                    self._create_reminder_task(message.channel, bumper)
                else:
                    log.warning("Cannot create reminder: Message channel is not a TextChannel.")

            except (discord.HTTPException, ValueError, TypeError, AttributeError):
                log.exception("Error processing bump reward and reminder.")
        else:
            log.debug(f"Message {message.id} embed did not match bump regex. Description: '{embed.description}'")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for new messages to detect a successful bump."""
        await self._process_bump_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Listen for message edits to detect a successful bump.
        This is crucial as many bots edit their messages to add embeds.
        """
        # We only care about the final state of the message.
        await self._process_bump_message(after)

    def _create_reminder_task(
        self,
        channel: discord.TextChannel,
        bumper: discord.User | discord.Member,
    ) -> None:
        """Create a non-blocking background task for the bump reminder."""
        task = asyncio.create_task(self._send_reminder(channel, bumper))
        task.add_done_callback(
            lambda t: (t.result() if t.exception() is None else log.error(f"Reminder task failed: {t.exception()}")),
        )

    async def _send_reminder(
        self,
        channel: discord.TextChannel,
        bumper: discord.User | discord.Member,
    ) -> None:
        """Wait for the specified time and then sends a reminder."""
        await asyncio.sleep(BUMP_REMINDER_DELAY_SECONDS)
        log.info("Sending bump reminder to %s in #%s.", bumper.display_name, channel.name)
        try:
            reminder_embed = discord.Embed(
                title="⏰ Time to Bump! ⏰",
                description=f"Hey {bumper.mention}, it's time to bump the server again!\n\nPlease use the `/bump` command.",
                color=discord.Colour.blue(),
            )
            await channel.send(embed=reminder_embed)
        except (discord.HTTPException, discord.Forbidden):
            log.exception("Failed to send reminder to %s.", channel.name)


async def setup(bot: "CurrencyBot") -> None:
    """Load the cog."""
    # Ensure DISBOARD_BOT_ID is valid before loading
    if not isinstance(DISBOARD_BOT_ID, int) or DISBOARD_BOT_ID == 0:
        log.error(
            "BumpHandlerCog not loaded because DISBOARD_BOT_ID is not configured correctly.",
        )
        return
    await bot.add_cog(BumpHandlerCog(bot))
