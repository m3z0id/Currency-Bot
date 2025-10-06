import logging

import discord
from discord import app_commands
from discord.ext import commands

from modules.CurrencyBot import CurrencyBot
from modules.enums import StatName

log = logging.getLogger(__name__)


class Bal(commands.Cog):
    bot: CurrencyBot

    def __init__(self, bot: CurrencyBot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="bal", description="Displays a user's balance")
    @app_commands.describe(member="User whose balance to show")
    async def bal(self, ctx: commands.Context, member: discord.Member = None) -> None:
        if member is None:
            member = ctx.author

        currency_balance = await self.bot.stats_db.get_stat(member.id, StatName.CURRENCY)
        bump_count = await self.bot.stats_db.get_stat(member.id, StatName.BUMPS)
        description = f"{member.mention}\nWallet: ${currency_balance:,}"
        if bump_count > 0:
            description += f"\nBumps: {bump_count}"

        embed = discord.Embed(
            title="Balance",
            description=description,
            color=discord.Colour.green(),
        )
        embed.set_author(name=member.name, icon_url=member.display_avatar)
        embed.set_footer(text=f"{ctx.author.display_name} | Balance")
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
        log.info(
            "Bal command executed by %s for %s.",
            ctx.author.display_name,
            member.display_name,
        )


async def setup(bot: CurrencyBot) -> None:
    await bot.add_cog(Bal(bot))
