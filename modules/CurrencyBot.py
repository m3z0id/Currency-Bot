import logging
import pathlib
from typing import ClassVar

import discord
from discord import Forbidden, HTTPException, MissingApplicationID
from discord.app_commands import CommandSyncFailure, TranslationError
from discord.ext import commands, tasks
from discord.ext.commands import (
    ExtensionAlreadyLoaded,
    ExtensionFailed,
    ExtensionNotFound,
    NoEntryPointError,
)

from modules.CurrencyDB import CurrencyDB
from modules.Database import Database
from modules.UserDB import UserDB

log = logging.getLogger(__name__)


class CurrencyBot(commands.Bot):
    loaded_extensions: ClassVar[list[str]] = []

    def __init__(self) -> None:
        # Define the bot and its command prefix
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    # Event to notify when the bot has connected
    async def setup_hook(self) -> None:
        log.info("Logged in as %s", self.user)

        # Initialize the database first
        self.database: Database = Database()
        self.currency_db = CurrencyDB(self.database)
        self.user_db = UserDB(self.database)

        # AWAIT the post-initialization tasks to ensure tables are created
        await self.currency_db.post_init()
        await self.user_db.post_init()
        log.info("Database tables initialized.")

        # Now it's safe to load cogs
        try:
            # Add 'cogs.' prefix to the path for loading
            for file in pathlib.Path("cogs/").glob("*.py"):
                if file.is_file():
                    await self.load_extension(f"cogs.{file.stem}")
                    log.info("Loaded %s", file.stem)
            synced = await self.tree.sync()  # Sync slash commands with Discord
            log.info("Synced %d command(s) %s", len(synced), [i.name for i in synced])
        except (
            HTTPException,
            CommandSyncFailure,
            Forbidden,
            MissingApplicationID,
            TranslationError,
            ExtensionNotFound,
            ExtensionAlreadyLoaded,
            NoEntryPointError,
            ExtensionFailed,
        ):
            log.exception("Error syncing commands")

        # Start the reminder background task
        self.reminder_task.start()
        log.info("Reminder background task started.")

    @tasks.loop(minutes=5)
    async def reminder_task(self) -> None:
        """Background task that handles sending daily reminders."""
        try:
            users_to_remind = await self.user_db.get_users_ready_for_reminder()
            for user_id, preference in users_to_remind:
                try:
                    user = await self.fetch_user(user_id)
                    if user:
                        await user.send(
                            "⏰ Your daily reward is ready to claim! Use `/daily` to get your reward.",
                        )
                        log.info("Sent daily reminder to user %d", user_id)

                        # Clear the cooldown timestamp to prevent re-sending reminders until the next /daily command.
                        await self.user_db.clear_daily_cooldown(user_id)

                        if preference == "ONCE":
                            await self.user_db.reset_one_time_reminder(user_id)
                            log.info("Reset one-time reminder for user %d", user_id)

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                    discord.NotFound,
                ):
                    log.exception("Error sending reminder to user %d", user_id)

        except (discord.HTTPException, ConnectionError, OSError):
            log.exception("Error in reminder background task")
