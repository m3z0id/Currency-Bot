import logging
import os
import random
import string
from typing import Final

from discord import Object, app_commands
from discord.ext import commands

from modules.enums import StatName
from modules.KiwiBot import KiwiBot
from modules.types import GuildId, PositiveInt, UserId

log = logging.getLogger(__name__)

# --- Configuration ---
# Define your guild ID here. Using a constant is best practice.
GUILD_ID: Final[int] = int(os.getenv("SWL_GUILD_ID", "0"))
GUILD: Final[Object] = Object(GUILD_ID)
COOLDOWN: Final[int] = 3600 * 6  # 6 hours


# Restricted to guild
@app_commands.guilds(GUILD)
class Sell(commands.Cog):
    LIMBS: Final[tuple[str, ...]] = (
        "Left Arm",
        "Right Arm",
        "Left Hand",
        "Right Hand",
        "Head",
        "Torso",
    )
    ORGANS: Final[tuple[str, ...]] = (
        "Brain",
        "Heart",
        "Left Kidney",
        "Right Kidney",
        "Left Lung",
        "Right Lung",
        "Liver",
        "Bone Marrow",
    )

    def __init__(self, bot: KiwiBot) -> None:
        self.bot = bot

    # This check applies to all text-based commands in this cog,
    # ensuring they only run in the specified guild.
    async def cog_check(self, ctx: commands.Context) -> bool:
        """Verify that the command is used in the correct guild.

        Args:
            ctx: The command context.

        Returns:
            True if the guild is correct, otherwise False.

        """
        return ctx.guild is not None and ctx.guild.id == self.bot.config.swl_guild_id

    async def _process_sale(
        self,
        ctx: commands.Context,
        item: str | None,
        item_list: tuple[str, ...],
        action_name: str,
    ) -> None:
        """Handle the sale/harvest logic.

        Args:
            ctx: The command context.
            item: The specific item to sell/harvest, or None to choose randomly.
            item_list: The tuple of available items.
            action_name: The name of the action being performed.

        """
        if item is None:
            item = random.choice(item_list)

        item = string.capwords(item.replace("_", " "))
        if item not in item_list:
            item_type = "limb" if item_list is self.LIMBS else "organ"
            await ctx.send(f"Invalid {item_type}", ephemeral=True)
            return

        if random.choice((True, False)):
            await ctx.send("You got caught by the police and made no money.")
            return

        random_num = PositiveInt(random.randint(1, 20))
        guild_id = GuildId(ctx.guild.id)
        user_id = UserId(ctx.author.id)
        await self.bot.user_db.increment_stat(
            user_id,
            guild_id,
            StatName.CURRENCY,
            random_num,
        )

        log.info(
            "User %s has sold wndx2's %s for %d.",
            ctx.author.display_name,
            item.lower(),
            random_num,
        )
        await ctx.send(
            f"{ctx.author.mention}, you sold wndx2's {item.lower()} for ${random_num}.",
        )

        log.info(
            "%s command executed by %s.",
            action_name.capitalize(),
            ctx.author.display_name,
        )

    @commands.hybrid_command(name="sell", description="Sell one of wndx2's limbs")
    @commands.cooldown(1, COOLDOWN, commands.BucketType.user)
    @app_commands.describe(limb="Limb to sell")
    @app_commands.choices(
        limb=[app_commands.Choice(name=limb, value=limb.lower()) for limb in LIMBS],
    )
    async def sell(self, ctx: commands.Context, limb: str | None = None) -> None:
        """Sell a limb."""
        await self._process_sale(ctx, limb, self.LIMBS, "sell")

    @commands.hybrid_command(
        name="harvest",
        description="Harvest one of wndx2's organs",
    )
    @commands.cooldown(1, COOLDOWN, commands.BucketType.user)
    @app_commands.describe(organ="Organ to harvest")
    @app_commands.choices(
        organ=[app_commands.Choice(name=organ, value=organ.lower()) for organ in ORGANS],
    )
    async def harvest(self, ctx: commands.Context, organ: str | None = None) -> None:
        """Harvest an organ."""
        await self._process_sale(ctx, organ, self.ORGANS, "harvest")


async def setup(bot: KiwiBot) -> None:
    """Set up the cog."""
    await bot.add_cog(Sell(bot))
