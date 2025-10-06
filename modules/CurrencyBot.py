import logging
import pathlib
from typing import Any, ClassVar

import discord
from discord import Forbidden, HTTPException, MissingApplicationID
from discord.app_commands import CommandSyncFailure, TranslationError
from discord.ext import commands
from discord.ext.commands import (
    ExtensionAlreadyLoaded,
    ExtensionFailed,
    ExtensionNotFound,
    NoEntryPointError,
)

from modules.Database import Database
from modules.StatsDB import StatsDB
from modules.TaskDB import TaskDB
from modules.UserDB import UserDB

log = logging.getLogger(__name__)


class CurrencyBot(commands.Bot):
    loaded_extensions: ClassVar[list[str]] = []

    def __init__(self) -> None:
        # Define the bot and its command prefix
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    # Event to notify when the bot has connected
    async def setup_hook(self) -> None:
        log.info("Logged in as %s", self.user)

        # Initialize the database first
        self.database: Database = Database()
        self.stats_db = StatsDB(self.database)
        self.user_db = UserDB(self.database)
        self.task_db = TaskDB(self.database)

        # AWAIT the post-initialization tasks to ensure tables are created
        await self.stats_db.post_init()
        await self.user_db.post_init()
        await self.task_db.post_init()
        log.info("All database tables initialized.")

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

        log.info("Setup complete.")

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Log unhandled exceptions."""
        log.exception(
            "Unhandled exception in %s",
            event_method,
            extra={"*args": args, "**kwargs": kwargs},
        )

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Log unhandled command exceptions."""
        try:
            raise error  # noqa: TRY301
        except commands.CommandNotFound:
            pass  # Ignore commands that don't exist
        except commands.CommandOnCooldown as e:
            await ctx.send(
                f"This command is on cooldown. Try again in {e.retry_after:.2f}s.",
                ephemeral=True,
            )
        except commands.MissingPermissions as e:
            await ctx.send(
                f"You're missing the permissions to run this command: {', '.join(e.missing_permissions)}",
                ephemeral=True,
            )
        except Exception:
            log.exception("Unhandled command error in '%s'", ctx.command)
            await ctx.send("An unexpected error occurred.", ephemeral=True)
