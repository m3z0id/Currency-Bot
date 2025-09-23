# new_daily.py
"""Handle '/daily' command.

Uses ephemeral messages to reduce channel spam.
"""

import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    # This avoids circular imports while providing type hints for the bot class
    from modules.CurrencyBot import CurrencyBot

log = logging.getLogger(__name__)

# Set the cooldown to a standard 24 hours (86400 seconds)
cooldown = 86400

# Constants for magic values
JACKPOT_THRESHOLD = 1000


class DailyView(discord.ui.View):
    """An interactive UI for the ephemeral /daily command response.

    It provides buttons for reminder preferences and sharing the result publicly.
    """

    def __init__(
        self,
        bot: "CurrencyBot",
        owner_id: int,
        daily_mon: int,
        new_balance: int,
        author: discord.User,
        channel: discord.TextChannel,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        self.daily_mon = daily_mon
        self.new_balance = new_balance
        self.author = author
        self.channel = channel

    def _create_share_embed(self) -> discord.Embed:
        """Create the public embed for when a user shares their daily claim."""
        is_jackpot = self.daily_mon > JACKPOT_THRESHOLD
        if is_jackpot:
            title = "🎉 JACKPOT! 🎉"
            description = f"**{self.author.mention} hit the jackpot and received ${self.daily_mon:,}!**"
            color = discord.Colour.orange()
        else:
            title = "💰 Daily Claim! 💰"
            description = f"{self.author.mention} received **${self.daily_mon:,}**!"
            color = discord.Colour.gold()

        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="New Balance", value=f"${self.new_balance:,}")
        embed.set_author(name=self.author.name, icon_url=self.author.display_avatar)
        embed.set_footer(text="What will they buy with it?")
        embed.timestamp = discord.utils.utcnow()
        return embed

    async def _update_reminder_preference(
        self,
        interaction: discord.Interaction,
        preference: str,
    ) -> None:
        """Handle the logic for all reminder button clicks."""
        messages = {
            "ONCE": "Success! You will be reminded for your next claim.",
            "ALWAYS": "Success! Your reminder preference is now set to 'Always'.",
            "NEVER": "Success! Reminders have been disabled.",
        }
        await self.bot.user_db.set_daily_reminder_preference(self.owner_id, preference)
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(messages[preference], ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure that only the user who ran the command can interact with the view."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your daily claim UI.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Remind Me Once",
        style=discord.ButtonStyle.secondary,
        emoji="⏰",
    )
    async def remind_once_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self._update_reminder_preference(interaction, "ONCE")

    @discord.ui.button(
        label="Always Remind Me",
        style=discord.ButtonStyle.success,
        emoji="🔁",
    )
    async def always_remind_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self._update_reminder_preference(interaction, "ALWAYS")

    @discord.ui.button(
        label="Disable Reminders",
        style=discord.ButtonStyle.danger,
        emoji="🔕",
    )
    async def disable_reminders_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self._update_reminder_preference(interaction, "NEVER")

    @discord.ui.button(
        label="Share to Channel",
        style=discord.ButtonStyle.primary,
        emoji="📢",
    )
    async def share_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        embed = self._create_share_embed()
        await self.channel.send(embed=embed)  # Use the stored channel for robustness

        button.disabled = True
        button.label = "Shared!"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "Your daily claim has been shared!",
            ephemeral=True,
        )


class Daily(commands.Cog):
    def __init__(self, bot: "CurrencyBot") -> None:
        self.bot = bot

    @commands.hybrid_command(name="daily", description="Claim your daily currency.")
    @commands.cooldown(1, cooldown, commands.BucketType.user)
    async def daily(self, ctx: commands.Context) -> None:
        # Check database cooldown before proceeding to prevent bypass on bot restart
        cooldown_str = await self.bot.user_db.get_daily_cooldown(ctx.author.id)
        if cooldown_str:
            try:
                # Parse the ISO format timestamp from database
                cooldown_end = discord.utils.parse_time(cooldown_str)
                if cooldown_end and discord.utils.utcnow() < cooldown_end:
                    # Cooldown is still active, show error message
                    (cooldown_end - discord.utils.utcnow()).total_seconds()
                    timestamp = int(cooldown_end.timestamp())
                    embed = discord.Embed(
                        title="Cooldown Active",
                        description=f"You can claim your next daily <t:{timestamp}:R> (at <t:{timestamp}:f>).",
                        color=discord.Colour.red(),
                    )
                    await ctx.send(embed=embed, ephemeral=True)
                    return
            except (ValueError, TypeError):
                # If parsing fails, continue with normal flow
                log.warning(
                    "Failed to parse cooldown timestamp for user %s: %s",
                    ctx.author.id,
                    cooldown_str,
                )

        rewards = [(50, 100), (101, 10000)]
        weights = [99, 1]
        chosen_range = random.choices(rewards, weights=weights, k=1)[0]
        daily_mon = random.randint(*chosen_range)

        await self.bot.currency_db.add_money(ctx.author.id, daily_mon)
        new_balance = await self.bot.currency_db.get_balance(ctx.author.id)

        cooldown_ends = discord.utils.utcnow() + timedelta(seconds=cooldown)
        await self.bot.user_db.set_daily_cooldown(
            ctx.author.id,
            cooldown_ends.isoformat(),
        )

        log.info(
            "User %s claimed $%s, new balance is $%s",
            ctx.author.display_name,
            daily_mon,
            new_balance,
        )

        next_claim_timestamp = int(cooldown_ends.timestamp())
        title = "🎉 Daily Claim Successful! 🎉"
        if daily_mon > JACKPOT_THRESHOLD:
            title = "🎊 JACKPOT! 🎊"

        response_content = (
            f"### {title}\n"
            f"You have received **${daily_mon:,}**!\n"
            f"Your new balance is **${new_balance:,}**.\n\n"
            f"Your next claim is available <t:{next_claim_timestamp}:R>."
        )

        view = DailyView(
            bot=self.bot,
            owner_id=ctx.author.id,
            daily_mon=daily_mon,
            new_balance=new_balance,
            author=ctx.author,
            channel=ctx.channel,
        )

        await ctx.send(response_content, view=view, ephemeral=True)

    @daily.error
    async def daily_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            cooldown_end_time = discord.utils.utcnow() + timedelta(
                seconds=error.retry_after,
            )
            timestamp = int(cooldown_end_time.timestamp())

            embed = discord.Embed(
                title="Cooldown Active",
                description=f"You can claim your next daily <t:{timestamp}:R> (at <t:{timestamp}:f>).",
                color=discord.Colour.red(),
            )
            await ctx.send(embed=embed, ephemeral=True)
        else:
            log.error("An unexpected error occurred in the daily command: %s", error)
            await ctx.send(
                "An unexpected error occurred. Please try again later.",
                ephemeral=True,
            )


async def setup(bot: "CurrencyBot") -> None:
    """Add the Daily cog to the bot."""
    await bot.add_cog(Daily(bot))
