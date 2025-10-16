import logging
import pathlib
from typing import ClassVar, Final

import discord
from discord import Forbidden, HTTPException, MissingApplicationID
from discord.app_commands import CommandSyncFailure, TranslationError
from discord.ext import commands
from discord.ext.commands import ExtensionAlreadyLoaded, ExtensionFailed, ExtensionNotFound, NoEntryPointError

from modules.config import BotConfig
from modules.ConfigDB import ConfigDB
from modules.Database import Database
from modules.InvitesDB import InvitesDB
from modules.TaskDB import TaskDB
from modules.TransactionsDB import TransactionsDB
from modules.UserDB import UserDB

log = logging.getLogger(__name__)


class KiwiBot(commands.Bot):
    loaded_extensions: ClassVar[list[str]] = []

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        # Define the bot and its command prefix
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        intents.reactions = True
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=None,
        )

    # Event to notify when the bot has connected
    async def setup_hook(self) -> None:
        log.info("Logged in as %s", self.user)

        # Initialize the database first
        self.database: Database = Database()
        self.user_db = UserDB(self.database)
        self.task_db = TaskDB(self.database)
        self.invites_db = InvitesDB(self.database)
        self.transactions_db = TransactionsDB(self.database)
        self.config_db = ConfigDB(self.database)

        # AWAIT the post-initialization tasks to ensure tables are created
        # UserDB must be first as other tables have foreign keys to it.
        await self.user_db.post_init()
        await self.task_db.post_init()
        await self.invites_db.post_init()
        await self.transactions_db.post_init()
        await self.config_db.post_init()
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

        if self.config.swl_guild_id:
            SWL_GUILD: Final[discord.Object] = discord.Object(self.config.swl_guild_id)
            synced_guild = await self.tree.sync(guild=SWL_GUILD)
            if synced_guild:
                log.info(
                    "Synced %d command(s) for guild %d: %s",
                    len(synced_guild),
                    self.config.swl_guild_id,
                    [i.name for i in synced_guild],
                )

        log.info("Setup complete.")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Send a welcome and setup guide when joining a new guild."""
        log.info("Joined new guild: %s (%s)", guild.name, guild.id)

        # Try to send a message to the system channel, which is usually the best bet.
        # Fallback to the first available text channel if the system channel isn't usable.
        target_channel = guild.system_channel
        if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break

        if target_channel:
            embed = discord.Embed(
                title="👋 Quick Setup!",
                description="Admins use the `/config autodiscover` command. I'll suggest settings for you to approve.",
                color=discord.Colour.green(),
            )
            await target_channel.send(embed=embed)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Handle data cleanup when the bot is removed from a guild."""
        log.info("Bot removed from guild: %s (%s). Cleaning up data.", guild.name, guild.id)
        await self.config_db.on_guild_remove(guild.id)

    async def on_error(
        self,
        event_method: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Log unhandled exceptions."""
        log.exception(
            "Unhandled exception in %s",
            event_method,
            extra={"*args": args, "**kwargs": kwargs},
        )

    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
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

    async def close(self) -> None:
        """Gracefully close bot resources."""
        # aiosqlite connections are managed by context managers,
        # so no explicit database closing is needed here.
        # This is a good place for other cleanup logic in the future.
        log.info("Closing bot gracefully.")
        await super().close()
