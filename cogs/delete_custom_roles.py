import datetime
import logging

import discord
from discord.ext import commands, tasks

# Set up logging for this cog
log = logging.getLogger(__name__)

# The prefix for roles that should be considered for pruning
ROLE_PREFIX = "Custom: "
# How many days old a role must be before it can be pruned
PRUNE_AFTER_DAYS = 30


class RolePrunerCog(commands.Cog):
    """A cog that automatically prunes old roles with a specific prefix."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
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
        cutoff_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=PRUNE_AFTER_DAYS)

        # Iterate over all the guilds the bot is in
        for guild in self.bot.guilds:
            log.info("Checking roles in guild: %s", guild.name)

            # Find all roles that meet the pruning criteria
            roles_to_prune = [
                role
                for role in guild.roles
                if not role.managed and role.name.startswith(ROLE_PREFIX) and role.created_at < cutoff_date
            ]

            if not roles_to_prune:
                log.info("No roles to prune in %s.", guild.name)
                continue

            log.info("Found %d roles to prune in %s.", len(roles_to_prune), guild.name)

            # Prune the identified roles
            for role in roles_to_prune:
                try:
                    await role.delete(reason=f"Pruning old role created more than {PRUNE_AFTER_DAYS} days ago.")
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

    @prune_roles_loop.before_loop
    async def before_prune_loop(self) -> None:
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Add the cog to the bot."""
    await bot.add_cog(RolePrunerCog(bot))
