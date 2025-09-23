import discord
from discord.ext import commands

from modules.CurrencyBot import CurrencyBot


class Roles(commands.Cog):
    """A cog for listing and managing roles."""

    def __init__(self, bot: CurrencyBot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="listroles",
        description="Lists all roles, sorted by permissions.",
    )
    @commands.guild_only()  # This command can only be used in a server
    async def list_roles(self, ctx: commands.Context) -> None:
        """List all roles with permissions and those with none."""
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        roles_with_permissions = []
        roles_without_permissions = []

        for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
            # Bot integrations and @everyone
            if role.managed:
                continue
            if role.permissions == discord.Permissions.none():
                roles_without_permissions.append(role.mention)
            else:
                roles_with_permissions.append(role.mention)

            print(role.name, role.secondary_colour, role.tertiary_colour)

        # Create the response embed
        embed = discord.Embed(
            title=f"Roles in {ctx.guild.name}",
            color=discord.Colour.blue(),
        )

        if roles_with_permissions:
            embed.add_field(
                name="Roles with Permissions",
                value="\n".join(roles_with_permissions),
                inline=False,
            )
        else:
            embed.add_field(
                name="Roles with Permissions",
                value="No roles with permissions found.",
                inline=False,
            )

        if roles_without_permissions:
            embed.add_field(
                name="Roles with No Permissions",
                value="\n".join(roles_without_permissions),
                inline=False,
            )
        else:
            embed.add_field(
                name="Roles with No Permissions",
                value="No roles without permissions found.",
                inline=False,
            )

        await ctx.send(embed=embed)
        print(f"'listroles' command executed by {ctx.author.display_name}.\n")


async def setup(bot: CurrencyBot) -> None:
    """Load the Roles cog."""
    await bot.add_cog(Roles(bot))
