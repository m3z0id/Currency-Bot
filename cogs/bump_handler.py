import asyncio
import logging
import os
import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from modules.discord_utils import ping_online_role

if TYPE_CHECKING:
    from modules.CurrencyBot import CurrencyBot
    from modules.CurrencyDB import CurrencyDB
    from modules.UserDB import UserDB

log = logging.getLogger(__name__)

# --- Configuration ---
DISBOARD_BOT_ID = int(os.getenv("DISBOARD_BOT_ID"))
BUMPER_ROLE_ID = int(os.getenv("BUMPER_ROLE_ID"))

BUMP_REMINDER_DELAY_SECONDS = 2 * 60 * 60 - 2  # 2 hours (and 2 seconds early to get the person ready)


class BumpHandlerCog(commands.Cog):
    """Handle rewards and reminders for server bumps from bots like Disboard."""

    def __init__(self, bot: "CurrencyBot") -> None:
        self.bot = bot
        self.currency_db: CurrencyDB = bot.currency_db
        self.user_db: UserDB = bot.user_db

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for new messages to detect a successful bump."""
        if not message.guild or message.author.id != DISBOARD_BOT_ID:
            return

        if not message.embeds:
            return

        embed = message.embeds[0]
        if not embed.description:
            return

        # --- Rule 3: Parse Embed and Process Reward/Reminder ---
        if "Bump done!" not in embed.description:
            return

        log.info("Detected a successful bump in message %s.", embed)
        try:
            bumper_id = message.interaction_metadata.user.id
            # Use message.guild.get_member for efficiency if user is in cache,

            bumper: discord.User | discord.Member | None = message.guild.get_member(bumper_id)
            if not bumper:  # otherwise fetch from API.
                bumper: discord.User | discord.Member = await self.bot.fetch_user(bumper_id)

            reward = random.randint(50, 100)

            await self.currency_db.add_money(bumper_id, reward)
            log.info("Rewarded %s with $%d for bumping.", bumper.display_name, reward)

            await message.channel.send(
                f"🎉 Thanks for bumping, {bumper.mention}! You've received **${reward}**.",
            )

            log.info("Scheduling a 2-hour bump reminder for %d.", BUMPER_ROLE_ID)
            self._create_reminder_task(message.channel, bumper.mention)

        except (discord.HTTPException, discord.Forbidden):
            log.exception("Error processing bump reward and reminder.")

    def _create_reminder_task(
        self,
        channel: discord.TextChannel,
        last_bumper: str,
    ) -> None:
        """Create a non-blocking background task for the bump reminder."""
        task = asyncio.create_task(self._send_reminder(channel, last_bumper))
        task.add_done_callback(
            lambda t: (t.result() if t.exception() is None else log.exception("Reminder task failed")),
        )

    async def _send_reminder(
        self,
        channel: discord.TextChannel,
        last_bumper: str,
    ) -> None:
        """Wait for the specified time and then sends a reminder."""
        await asyncio.sleep(BUMP_REMINDER_DELAY_SECONDS)
        log.info("Sending bump reminder to #%s.", channel.name)
        try:
            reminder_embed = discord.Embed(
                title="⏰ Time to Bump ⏰",
                description=f"Thank you earlier {last_bumper}!",
                color=discord.Colour.blue(),
            )
            role = channel.guild.get_role(BUMPER_ROLE_ID)
            if role:
                await channel.send(f"Hey {await ping_online_role(role, self.user_db)}", embed=reminder_embed)
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
