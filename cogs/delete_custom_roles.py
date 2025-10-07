import datetime
import logging

import discord
from discord.ext import commands, tasks

from modules.KiwiBot import KiwiBot

# Set up logging for this cog
log = logging.getLogger(__name__)


class RolePrunerCog(commands.Cog):
    """A cog that automatically prunes old roles with a specific prefix."""

    def __init__(
        self,
        bot: KiwiBot,
        role_prefix: str,
        prune_after_days: int,
    ) -> None:
        self.bot = bot
        self.role_prefix = role_prefix
        self.prune_after_days = prune_after_days
        # Start the pruning loop as soon as the cog is loaded
        self.prune_roles_loop.start()

    def cog_unload(self) -> None:
        """Clean up when the cog is unloaded."""
        self.prune_roles_loop.cancel()

    @tasks.loop(hours=1)  # Hourly
    async def prune_roles_loop(self) -> None:
        """Iterate through all guilds and prunes roles that match the criteria."""
        log.info("Starting daily check for old custom roles to prune.")

        # Calculate the cutoff date for roles to be considered old
        cutoff_date = discord.utils.utcnow() - datetime.timedelta(days=self.prune_after_days)

        # Iterate over all the guilds the bot is in
        for guild in self.bot.guilds:
            log.info("Checking roles in guild: %s", guild.name)

            # Find all roles that meet the pruning criteria
            roles_to_prune = [
                role
                for role in guild.roles
                if not role.managed and role.name.startswith(self.role_prefix) and role.created_at < cutoff_date
            ]

            if not roles_to_prune:
                log.info("No roles to prune in %s.", guild.name)
                continue

            log.info("Found %d roles to prune in %s.", len(roles_to_prune), guild.name)

            # Prune the identified roles
            for role in roles_to_prune:
                try:
                    await role.delete(reason=f"Pruning old role created more than {self.prune_after_days} days ago.")
                    log.info("Successfully pruned role '%s' from %s.", role.name, guild.name)
                except discord.Forbidden:
                    log.exception(
                        "Failed to prune role '%s' in %s: Missing Permissions.",
                        role.name,
                        guild.name,
                    )
                except discord.HTTPException:
                    log.exception(
                        "Failed to prune role '%s' in %s due to an API error.",
                        role.name,
                        guild.name,
                    )

        log.info("Finished daily role pruning check.")


async def setup(bot: KiwiBot) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(
        RolePrunerCog(
            bot,
            role_prefix=bot.config.custom_role_prefix,
            prune_after_days=bot.config.custom_role_prune_days,
        ),
    )
