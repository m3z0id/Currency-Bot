import logging

import discord
from discord import Forbidden, HTTPException
from discord.ext import commands, tasks

from modules.KiwiBot import KiwiBot
from modules.types import GuildId, RoleId
from modules.UserDB import UserDB

# Use Python's logging module for better diagnostics in a reusable component
log = logging.getLogger(__name__)


class PrunerCog(commands.Cog):
    def __init__(
        self,
        bot: KiwiBot,
        user_db: UserDB,
        guild_id: GuildId,
        role_ids_to_prune: list[RoleId],
        inactivity_days: int = 14,
    ) -> None:
        self.bot = bot
        self.user_db = user_db
        self.guild_id = guild_id
        self.role_ids_to_prune = role_ids_to_prune
        self.inactivity_days = inactivity_days

    # This is a special event that runs when the cog is loaded
    async def cog_load(self) -> None:
        self.prune_loop.start()

    def cog_unload(self) -> None:
        """Clean up when the cog is unloaded."""
        self.prune_loop.cancel()

    @tasks.loop(hours=1)
    async def prune_loop(self) -> None:
        """Check for and prune inactive members."""
        log.info("Running automatic prune check for inactive members...")
        # Guard clause to ensure this only runs for the privileged guild.
        if not self.bot.get_guild(self.guild_id):
            log.info("Pruner loop skipped: privileged guild not found.")
            return

        guild = await self.bot.fetch_guild(self.guild_id)
        if not guild:
            log.error("Pruning failed: Guild with ID %s not found.", self.guild_id)
            return

        # Fetch roles from the guild that are configured for pruning
        prunable_roles = {guild.get_role(role_id) for role_id in self.role_ids_to_prune}
        prunable_roles.discard(None)  # Remove None if a role ID wasn't found
        if not prunable_roles:
            log.warning(
                "Pruning skipped: None of the configured roles found in guild '%s'.",
                guild.name,
            )

        # Get inactive user IDs from the database
        inactive_user_ids = await self.user_db.get_inactive_users(self.guild_id, self.inactivity_days)
        if not inactive_user_ids:
            log.info("No inactive users found in the database to prune.")
            return

        total_members_pruned = 0
        for user_id in inactive_user_ids:
            try:
                # Use fetch_member instead of get_member to ensure we get the member
                # even if they're not in the bot's cache
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                continue  # User is no longer in the guild, skip them
            except discord.HTTPException:
                log.exception("Failed to fetch member %s", user_id)
                continue

            if not member:
                continue  # User is not in the target guild

            # Find which of the prunable roles the member actually has
            roles_to_remove = [role for role in member.roles if role in prunable_roles]

            # Also add any roles that start with matching prefix
            gradient_roles = [
                role
                for role in member.roles
                if role.name.startswith("Colour: ") or role.name.startswith("Ping: ") or role.name.startswith("Gradient: ")
            ]
            roles_to_remove.extend(gradient_roles)

            # Remove duplicates in case a gradient role was already in prunable_roles
            roles_to_remove = [r for r in set(roles_to_remove) if not r.managed]

            if roles_to_remove:
                try:
                    await member.remove_roles(
                        *roles_to_remove,
                        reason=f"Pruned for {self.inactivity_days}+ days of inactivity.",
                    )
                    role_names = ", ".join(f"'{r.name}'" for r in roles_to_remove)
                    log.info("Pruned %s from %s.", role_names, member.display_name)
                    total_members_pruned += 1
                except Forbidden:
                    log.exception(
                        "Failed to prune %s: Missing Permissions.",
                        member.display_name,
                    )
                except HTTPException:
                    log.exception("Failed to prune %s", member.display_name)

        log.info(
            "Pruning complete. Roles removed from %s member(s).",
            total_members_pruned,
        )


# The setup function that discord.py calls when loading the extension
async def setup(bot: KiwiBot) -> None:
    # The cog needs the UserDB instance from the bot
    user_db = getattr(bot, "user_db", None)
    if not user_db:
        msg = "Bot is missing the 'user_db' attribute."
        raise RuntimeError(msg)

    if not bot.config.guild_id:
        log.error("PrunerCog not loaded: GUILD_ID is not configured.")
        return

    # Use the configuration from the BotConfig object
    pruner_cog = PrunerCog(
        bot=bot,
        user_db=user_db,
        guild_id=bot.config.guild_id,
        role_ids_to_prune=bot.config.roles_to_prune,
        inactivity_days=bot.config.inactivity_days,
    )
    await bot.add_cog(pruner_cog)
