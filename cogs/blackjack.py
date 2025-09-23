from __future__ import annotations

import asyncio
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

import discord
from blackjack21 import Card, Dealer, Player, PlayerBase, Table
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from discord import Interaction

    from modules.CurrencyBot import CurrencyBot


# --- Enums and Constants ---
class ActiveHand(Enum):
    """Cleanly manages which hand is active during a split."""

    PRIMARY = auto()
    SPLIT = auto()


class GameResult(Enum):
    """Represents the final outcome of a hand for stat tracking and payouts."""

    WIN = auto()
    LOSS = auto()
    PUSH = auto()
    BLACKJACK = auto()
    SURRENDER = auto()


# --- UI View: The Core of the Game ---
class BlackjackView(discord.ui.View):
    """Manages the entire game state, logic, and UI components for a single game."""

    GAME_TIMEOUT: ClassVar[float] = 180.0  # 3 minutes

    def __init__(
        self,
        bot: CurrencyBot,
        user: discord.User | discord.Member,
        bet: int,
    ) -> None:
        super().__init__(timeout=self.GAME_TIMEOUT)
        self.bot = bot
        self.user = user
        self.initial_bet = bet
        self.active_hand_state: ActiveHand = ActiveHand.PRIMARY
        self.last_action: str | None = None

        self.table = Table(players=[(user.display_name, bet)], auto_deal=True)
        self.player: Player = self.table.players[0]
        self.dealer: Dealer = self.table.dealer
        self.outcome_message: str | None = None

        # Handle instant blackjack on deal
        BLACKJACK = 21
        if BLACKJACK in (self.player.total, self.dealer.total):
            # We cannot `await` in __init__, so we run _end_game synchronously.
            # The async payout logic within _end_game will be spun off as a task.
            self._end_game()
        else:
            self._update_buttons()

    @property
    def total_bet_at_risk(self) -> int:
        """Calculates the total bet across all hands."""
        bet = self.player.bet  # Base bet, includes double down
        if self.player.split:
            bet += self.player.split.bet
        return bet

    @property
    def active_hand(self) -> Player | PlayerBase:
        """Returns the hand object that is currently being played."""
        if self.active_hand_state is ActiveHand.SPLIT and self.player.split:
            return self.player.split
        return self.player

    # --- Button and UI State Management ---
    def _update_buttons(self) -> None:
        """Clear and adds buttons based on the current game state."""
        self.clear_items()
        if self.outcome_message:  # Game is over
            self.add_item(PlayAgainButton(self.initial_bet))
            self.add_item(NewBetButton())
        else:  # Player's turn
            self.add_item(HitButton())
            self.add_item(StandButton())
            if len(self.player.hand) == 2 and not self.player.split:  # noqa: PLR2004
                self.add_item(SurrenderButton())
            if self.player.can_double_down:
                self.add_item(DoubleDownButton())
            if self.active_hand_state is ActiveHand.PRIMARY and self.player.can_split:
                self.add_item(SplitButton())

    def disable_all_buttons(self, is_disabled: bool = True) -> None:
        """Disables or enables all buttons in the view."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = is_disabled

    # --- Game Flow & Logic ---
    async def _resolve_payout_and_stats(
        self,
        result: GameResult,
        bet_amount: int,
    ) -> None:
        """Updates the bot's central stat tracker (in-memory) and processes
        the database transaction for the game's financial outcome.
        """
        guild_id = self.user.guild.id
        user_id = self.user.id

        # --- 1. Update In-Memory Stats ---
        if guild_id not in self.bot.blackjack_stats:
            self.bot.blackjack_stats[guild_id] = {}
        if user_id not in self.bot.blackjack_stats[guild_id]:
            self.bot.blackjack_stats[guild_id][user_id] = {
                "wins": 0,
                "losses": 0,
                "pushes": 0,
                "blackjacks": 0,
                "net_credits": 0,
            }

        stats = self.bot.blackjack_stats[guild_id][user_id]
        net_change = 0

        if result is GameResult.WIN:
            stats["wins"] += 1
            net_change = bet_amount
        elif result is GameResult.BLACKJACK:
            stats["blackjacks"] += 1
            net_change = int(bet_amount * 1.5)
        elif result is GameResult.LOSS:
            stats["losses"] += 1
            net_change = -bet_amount
        elif result is GameResult.SURRENDER:
            stats["losses"] += 1
            net_change = -(bet_amount // 2)
        elif result is GameResult.PUSH:
            stats["pushes"] += 1

        stats["net_credits"] += net_change

        # --- 2. Process Database Payout ---
        payout = 0
        if result is GameResult.WIN:
            payout = bet_amount * 2  # Return original bet + winnings
        elif result is GameResult.BLACKJACK:
            payout = bet_amount + int(
                bet_amount * 1.5,
            )  # Return original bet + 3:2 winnings
        elif result is GameResult.PUSH:
            payout = bet_amount  # Return original bet
        elif result is GameResult.SURRENDER:
            payout = bet_amount // 2  # Return half of original bet

        if payout > 0:
            await self.bot.currency_db.add_money(self.user.id, payout)

    def _end_game(self) -> None:
        """Determine winner, sets outcome message, and calls for payout/stat updates."""
        if not (self.dealer.stand or self.dealer.bust):
            self.dealer.play_dealer()

        def get_result(hand: Player | PlayerBase, bet: int) -> tuple[str, GameResult]:
            result = hand.result
            hand_name = "Split Hand" if hand is not self.player else "Main Hand"

            if result == -2:
                return (f"{hand_name}: Busted! You lose ${bet:,}.", GameResult.LOSS)
            if result == -1:
                return (
                    f"{hand_name}: Dealer wins. You lose ${bet:,}.",
                    GameResult.LOSS,
                )
            if result == 0:
                return (f"{hand_name}: It's a push! Bet returned.", GameResult.PUSH)
            if result == 1:
                return (
                    f"{hand_name}: Blackjack! You win ${int(bet * 1.5):,}.",
                    GameResult.BLACKJACK,
                )
            if result in (2, 3):
                return (f"{hand_name}: You win! You get ${bet:,}.", GameResult.WIN)
            return ("", GameResult.PUSH)

        main_text, main_result = get_result(self.player, self.player.bet)
        asyncio.create_task(
            self._resolve_payout_and_stats(main_result, self.player.bet),
        )

        if self.player.split:
            split_text, split_result = get_result(
                self.player.split,
                self.player.split.bet,
            )
            asyncio.create_task(
                self._resolve_payout_and_stats(split_result, self.player.split.bet),
            )
            self.outcome_message = f"{main_text}\n{split_text}"
        else:
            self.outcome_message = main_text

        self._update_buttons()

    async def _handle_stand_or_dd(self, interaction: Interaction) -> None:
        self.disable_all_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)
        await asyncio.sleep(1.5)
        self._end_game()
        await interaction.edit_original_response(embed=self.create_embed(), view=self)

    # --- Embed Creation ---
    def create_embed(self) -> discord.Embed:
        is_game_over = self.outcome_message is not None
        color = discord.Colour.blue()
        if is_game_over:
            (self.bot.blackjack_stats.get(self.user.guild.id, {}).get(self.user.id, {}).get("net_credits", 0))
            is_win = "win" in self.outcome_message.lower() or "get" in self.outcome_message.lower()
            color = discord.Colour.green() if is_win else discord.Colour.red()
            if "push" in self.outcome_message.lower():
                color = discord.Colour.light_grey()

        embed = discord.Embed(
            title=f"Blackjack | Total Bet: ${self.total_bet_at_risk:,}",
            color=color,
        )
        dealer_hand_val = self.dealer.total if is_game_over else self.dealer.hand[0].value
        dealer_hand_str = format_hand(self.dealer.hand, not is_game_over)
        embed.add_field(
            name="Dealer's Hand",
            value=f"{dealer_hand_str}\n**Total: {dealer_hand_val}**",
            inline=False,
        )

        p_active = "► " if self.active_hand_state is ActiveHand.PRIMARY and not is_game_over else ""
        embed.add_field(
            name=f"{p_active}{self.user.display_name}'s Hand",
            value=f"{format_hand(self.player.hand)}\n**Total: {self.player.total}**",
        )

        if self.player.split:
            s_active = "► " if self.active_hand_state is ActiveHand.SPLIT and not is_game_over else ""
            embed.add_field(
                name=f"{s_active}Split Hand",
                value=f"{format_hand(self.player.split.hand)}\n**Total: {self.player.split.total}**",
            )

        if self.outcome_message:
            embed.description = f"**{self.outcome_message}**"

        footer = "Game Over" if is_game_over else "It's your turn!"
        if self.last_action:
            footer += f" | {self.last_action}"
        embed.set_footer(text=footer)
        return embed


# --- Buttons ---
class HitButton(discord.ui.Button["BlackjackView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Hit",
            style=discord.ButtonStyle.secondary,
            emoji="➕",
        )

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        active_hand = view.active_hand
        card = active_hand.play_hit()
        view.last_action = f"You hit and drew a {card}."

        if active_hand.bust:
            view.last_action += " You busted!"
            if view.player.split and view.active_hand_state is ActiveHand.PRIMARY:
                view.active_hand_state = ActiveHand.SPLIT
                view._update_buttons()
                await interaction.response.edit_message(
                    embed=view.create_embed(),
                    view=view,
                )
            else:
                view._end_game()
                await interaction.response.edit_message(
                    embed=view.create_embed(),
                    view=view,
                )
        else:
            await interaction.response.edit_message(
                embed=view.create_embed(),
                view=view,
            )


class StandButton(discord.ui.Button["BlackjackView"]):
    def __init__(self) -> None:
        super().__init__(label="Stand", style=discord.ButtonStyle.primary, emoji="✋")

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        view.last_action = f"You stood with a total of {view.active_hand.total}."
        view.active_hand.play_stand()

        if view.player.split and view.active_hand_state is ActiveHand.PRIMARY:
            view.active_hand_state = ActiveHand.SPLIT
            view._update_buttons()
            await interaction.response.edit_message(
                embed=view.create_embed(),
                view=view,
            )
        else:
            await view._handle_stand_or_dd(interaction)


class DoubleDownButton(discord.ui.Button["BlackjackView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Double Down",
            style=discord.ButtonStyle.success,
            emoji="💰",
        )

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        # Check if user can afford to double down
        if (balance := await view.bot.currency_db.get_balance(interaction.user.id)) < view.initial_bet:
            await interaction.response.send_message(
                f"You don't have enough credits to double down. You need ${view.initial_bet:,} but only have ${balance:,}.",
                ephemeral=True,
            )
            return

        await view.bot.currency_db.remove_money(interaction.user.id, view.initial_bet)
        card = view.player.play_double_down()
        view.last_action = f"You doubled down and drew a {card}. Final total: {view.player.total}."
        await view._handle_stand_or_dd(interaction)


class SplitButton(discord.ui.Button["BlackjackView"]):
    def __init__(self) -> None:
        super().__init__(label="Split", style=discord.ButtonStyle.success, emoji="✌️")

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        # Check if user can afford to split
        if (balance := await view.bot.currency_db.get_balance(interaction.user.id)) < view.initial_bet:
            await interaction.response.send_message(
                f"You don't have enough credits to split. You need ${view.initial_bet:,} but only have ${balance:,}.",
                ephemeral=True,
            )
            return

        if view.player.can_split:
            await view.bot.currency_db.remove_money(
                interaction.user.id,
                view.initial_bet,
            )
            view.last_action = "You split your hand!"
            view.player.play_split()
            view._update_buttons()
            await interaction.response.edit_message(
                embed=view.create_embed(),
                view=view,
            )


class SurrenderButton(discord.ui.Button["BlackjackView"]):
    def __init__(self) -> None:
        super().__init__(label="Surrender", style=discord.ButtonStyle.danger, emoji="🏳️")

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        surrender_return = view.initial_bet // 2
        view.outcome_message = f"You surrendered. Half your bet (${surrender_return:,}) was returned."
        view.player._bust = True  # Internal way to mark a loss
        view.disable_all_buttons(True)

        await view._resolve_payout_and_stats(GameResult.SURRENDER, view.initial_bet)
        view._update_buttons()
        await interaction.response.edit_message(embed=view.create_embed(), view=view)


class PlayAgainButton(discord.ui.Button["BlackjackView"]):
    def __init__(self, bet: int) -> None:
        super().__init__(label="Play Again", style=discord.ButtonStyle.success)
        self.bet = bet

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        if (balance := await view.bot.currency_db.get_balance(interaction.user.id)) < self.bet:
            await interaction.response.edit_message(
                content=f"You can't play again. You need ${self.bet:,} but only have ${balance:,}.",
                embed=None,
                view=None,
            )
            return

        await view.bot.currency_db.remove_money(interaction.user.id, self.bet)
        new_view = BlackjackView(view.bot, interaction.user, self.bet)
        await interaction.response.edit_message(
            embed=new_view.create_embed(),
            view=new_view,
        )


class NewBetButton(discord.ui.Button["BlackjackView"]):
    def __init__(self) -> None:
        super().__init__(label="New Bet", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: Interaction) -> None:
        self.view.stop()
        await interaction.response.edit_message(
            content="Use the `/blackjack` command to start a new game with a new bet.",
            embed=None,
            view=None,
        )


# --- Helper & Cog ---
def format_hand(hand: list[Card], is_dealer_hidden: bool = False) -> str:
    """Formats cards with suit emojis for a richer display."""
    suits = {"Hearts": "♥️", "Diamonds": "♦️", "Spades": "♠️", "Clubs": "♣️"}

    def format_card(card: Card) -> str:
        return f"`{card.rank}{suits.get(card.suit, card.suit)}`"

    if is_dealer_hidden and len(hand) > 1:
        return f"{format_card(hand[0])} `[?]`"
    return " ".join(format_card(c) for c in hand) if hand else "`Empty`"


class BlackjackCog(commands.Cog):
    def __init__(self, bot: CurrencyBot) -> None:
        self.bot = bot
        # In-memory stat tracking. For persistence, you'd use a database.
        self.bot.blackjack_stats: dict[int, dict[int, dict[str, int]]] = {}

    @commands.hybrid_command(
        name="blackjack",
        description="Start a private game of Blackjack.",
    )
    @app_commands.describe(bet="The amount of credits you want to bet.")
    async def blackjack(self, ctx: commands.Context, bet: commands.Range[int, 1]) -> None:
        if (balance := await self.bot.currency_db.get_balance(ctx.author.id)) < bet:
            await ctx.send(
                f"Insufficient funds! You tried to bet ${bet:,} but only have ${balance:,}.",
                ephemeral=True,
            )
            return

        await self.bot.currency_db.remove_money(ctx.author.id, bet)
        view = BlackjackView(self.bot, ctx.author, bet)
        await ctx.send(embed=view.create_embed(), view=view, ephemeral=False)

    @commands.hybrid_command(
        name="blackjack-stats",
        description="View your blackjack statistics for this server.",
    )
    async def blackjack_stats(self, ctx: commands.Context) -> None:
        stats = self.bot.blackjack_stats.get(ctx.guild.id, {}).get(ctx.author.id)
        if not stats:
            await ctx.send("You haven't played any games yet!", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{ctx.author.display_name}'s Blackjack Stats",
            color=discord.Colour.gold(),
        )
        total_games = stats["wins"] + stats["losses"] + stats["pushes"] + stats["blackjacks"]
        win_rate = ((stats["wins"] + stats["blackjacks"]) / total_games * 100) if total_games > 0 else 0

        embed.add_field(name="Total Games", value=f"{total_games}")
        embed.add_field(name="Win Rate", value=f"{win_rate:.2f}%")
        embed.add_field(name="Pushes", value=f"{stats['pushes']}")
        embed.add_field(name="Wins", value=f"{stats['wins']}")
        embed.add_field(name="Losses", value=f"{stats['losses']}")
        embed.add_field(name="Blackjacks", value=f"{stats['blackjacks']}")
        embed.add_field(name="Net Credits", value=f"{stats['net_credits']:,}")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="blackjack-leaderboard",
        description="View the server's blackjack leaderboard.",
    )
    async def blackjack_leaderboard(self, ctx: commands.Context) -> None:
        guild_stats = self.bot.blackjack_stats.get(ctx.guild.id)
        if not guild_stats:
            await ctx.send(
                "No one has played any games on this server yet!",
                ephemeral=True,
            )
            return

        sorted_players = sorted(
            guild_stats.items(),
            key=lambda item: item[1]["net_credits"],
            reverse=True,
        )
        embed = discord.Embed(
            title="Blackjack Leaderboard",
            description="Top players by net credits won.",
            color=discord.Colour.gold(),
        )
        for i, (user_id, stats) in enumerate(sorted_players[:10]):
            user = ctx.guild.get_member(user_id)
            user_display = user.display_name if user else f"User ID: {user_id}"
            embed.add_field(
                name=f"{i + 1}. {user_display}",
                value=f"**Net Credits:** {stats['net_credits']:,}",
                inline=False,
            )

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: CurrencyBot) -> None:
    await bot.add_cog(BlackjackCog(bot))
