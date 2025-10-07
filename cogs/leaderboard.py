# In cogs/leaderboard.py
import discord
from discord import app_commands
from discord.ext import commands

from modules.CurrencyBot import CurrencyBot
from modules.enums import StatName


class Leaderboard(commands.Cog):
    def __init__(self, bot: CurrencyBot) -> None:
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Displays the server leaderboard for a specific stat.")
    @app_commands.choices(
        stat=[
            app_commands.Choice(name="💰 Currency", value=StatName.CURRENCY.value),
            app_commands.Choice(name="⬆️ Bumps", value=StatName.BUMPS.value),
            app_commands.Choice(name="⭐ XP", value=StatName.XP.value),
        ],
    )
    async def leaderboard(self, interaction: discord.Interaction, stat: app_commands.Choice[str]) -> None:
        await interaction.response.defer()

        # Fetch the leaderboard data from the database
        stat_enum = StatName(stat.value)
        top_users = await self.bot.stats_db.get_leaderboard(stat_enum, limit=10)

        if not top_users:
            await interaction.followup.send(f"Nobody is on the {stat.name} leaderboard yet!", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{stat.name} Leaderboard",
            color=discord.Colour.gold(),
            timestamp=discord.utils.utcnow(),
        )

        description = []
        for rank, (user_id, value) in enumerate(top_users, 1):
            try:
                member = await interaction.guild.fetch_member(user_id)
                user_display = member.mention
            except discord.NotFound:
                user_display = f"*(User Not Found: `{user_id}`)*"

            prefix = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"**{rank}.**")
            description.append(f"{prefix} {user_display}: `{value:,}`")

        embed.description = "\n".join(description)
        await interaction.followup.send(embed=embed)


async def setup(bot: CurrencyBot) -> None:
    await bot.add_cog(Leaderboard(bot))
