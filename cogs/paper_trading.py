# In cogs/paper_trading.py
from __future__ import annotations  # Defer type annotation evaluation

import logging
from typing import TYPE_CHECKING

# --- Discord Imports ---
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt  # For formatting timestamps

# --- Local Imports ---
from modules.trading_logic import (
    ALLOWED_STOCKS,
    InsufficientFundsError,
    PortfolioNotFoundError,
    PriceNotAvailableError,
    TradingLogic,
)

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot
    from modules.types import GuildId


# --- Type Hinting ---
type Ticker = str
type Quantity = int | float
type Price = float

# --- Logging ---
log = logging.getLogger(__name__)

# --- Choices for App Commands ---
ALLOWED_TICKER_CHOICES = [app_commands.Choice(name=ticker, value=ticker) for ticker in sorted(ALLOWED_STOCKS)]


# --- Discord Cog ---
@app_commands.guild_only()
class PaperTradingCog(commands.Cog):
    """Discord Cog for paper trading frontend interactions."""

    def __init__(self, bot: KiwiBot) -> None:
        self.bot = bot
        # Get the initialized TradingLogic instance from the bot
        if not bot.trading_logic:
            msg = "TradingLogic not initialized on bot before cog setup."
            raise RuntimeError(msg)
        self.trading_logic: TradingLogic = self.bot.trading_logic
        log.info("PaperTradingCog initialized with bot's TradingLogic instance.")

    # --- Helper to ensure guild context ---
    async def _ensure_guild_context(self, ctx: commands.Context) -> GuildId | None:
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.", ephemeral=True)
            return None
        return ctx.guild.id

    # --- Helper for Error Handling ---

    async def _handle_trading_errors(
        self,
        ctx: commands.Context,
        error: Exception,
    ) -> None:
        """Reply to common trading errors."""
        ephemeral = True  # Usually want trading errors private
        target = ctx

        # Add the new error types to the list of "safe" errors
        if isinstance(
            error,
            (
                InsufficientFundsError,
                PortfolioNotFoundError,
                PriceNotAvailableError,  # Added
                ValueError,
            ),
        ):
            await target.send(f"❌ Error: {error}", ephemeral=ephemeral)
        elif isinstance(error, commands.CommandError):
            log.warning("CommandError during trading operation: %s", error)
            await target.send(
                f"⚠️ An application error occurred: {error}",
                ephemeral=ephemeral,
            )
        elif isinstance(error, ConnectionError):
            log.warning("ConnectionError during trading operation: %s", error)
            await target.send(f"📡 Error: {error}", ephemeral=ephemeral)
        else:  # Catch unexpected errors
            log.exception("Unexpected error during trading operation: %s", error)
            await target.send(
                "🆘 An unexpected internal error occurred. Please try again later.",
                ephemeral=ephemeral,
            )

    # --- Commands ---
    @commands.hybrid_command(name="buy", description="Buy shares of a stock.")
    @app_commands.choices(ticker=ALLOWED_TICKER_CHOICES)
    @app_commands.describe(
        ticker="Symbol (e.g., TQQQ).",
        amount="The dollar amount to invest.",
    )
    async def buy(
        self,
        ctx: commands.Context,
        ticker: str,
        amount: commands.Range[int, 1],  # ty: ignore [invalid-type-form]
    ) -> None:
        """Handle the buy/cover command, calling middleware."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        try:
            (
                filled_price,
                trade_amount,
                action,
                timestamp,
                was_stacked,
                total_invested,
            ) = await self.trading_logic.open_position(
                user_id=ctx.author.id,
                guild_id=guild_id,
                ticker=ticker,
                trade_type="BUY",
                dollar_amount=amount,
            )

            if was_stacked:
                response_content = (
                    f"✅ **{action} (Stacked)**\n"
                    f"Added **${trade_amount:,.2f}** to your existing {ticker.upper()} position @ **${filled_price:,.2f}**\n"
                    f"New position total: **${total_invested:,.2f}**\n"
                    f"(Price as of {format_dt(timestamp, 'R')})"
                )
            else:
                response_content = (
                    f"✅ **{action}**\n"
                    f"Opened new **${trade_amount:,.2f}** position in {ticker.upper()} @ **${filled_price:,.2f}**\n"
                    f"(Price as of {format_dt(timestamp, 'R')})\n"
                    f"Your cash balance has been debited by ${trade_amount:,.2f}."
                )

            if not self.trading_logic.is_market_open():
                response_content += "\n\n⚠️ **Note: The market is closed. Position opened at the last available price.**"

            await ctx.send(response_content, ephemeral=True)

        except Exception as e:  # noqa: BLE001
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(
        name="short",
        description="Open a new short position in a stock.",
    )
    @app_commands.choices(ticker=ALLOWED_TICKER_CHOICES)
    @app_commands.describe(
        ticker="Symbol (e.g., TQQQ).",
        amount="The dollar amount to short (collateral).",
    )
    async def short(
        self,
        ctx: commands.Context,
        ticker: str,
        amount: commands.Range[int, 1],  # ty: ignore [invalid-type-form]
    ) -> None:
        """Handle the short-sell command, calling middleware."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        try:
            (
                filled_price,
                trade_amount,  # This will be -100 for a $100 short
                action,
                timestamp,
                was_stacked,
                total_invested,
            ) = await self.trading_logic.open_position(
                user_id=ctx.author.id,
                guild_id=guild_id,
                ticker=ticker,
                trade_type="SHORT",
                dollar_amount=amount,
            )

            if was_stacked:
                response_content = (
                    f"✅ **{action} (Stacked)**\n"
                    f"Added **${abs(trade_amount):,.2f}** to your existing {ticker.upper()} short position @ **${filled_price:,.2f}**\n"  # noqa: E501
                    f"New position total: **${total_invested:,.2f}**\n"
                    f"(Price as of {format_dt(timestamp, 'R')})"
                )
            else:
                response_content = (
                    f"✅ **{action}**\n"
                    f"Opened new **${trade_amount:,.2f}** position in {ticker.upper()} @ **${filled_price:,.2f}**\n"
                    f"(Price as of {format_dt(timestamp, 'R')})\n"
                    f"Your cash balance has been debited by **${amount:,.2f}** for collateral."
                )

            if not self.trading_logic.is_market_open():
                response_content += "\n\n⚠️ **Note: The market is closed. Position opened at the last available price.**"

            await ctx.send(response_content, ephemeral=True)

        except Exception as e:  # noqa: BLE001
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(
        name="close",
        description="Close an open position by its ID.",
    )
    @app_commands.describe(
        position_id="The ID of the position (from /portfolio).",
        amount="The dollar amount of your *original investment* to close. (Optional: closes all if omitted)",
    )
    async def close(
        self,
        ctx: commands.Context,
        position_id: int,
        amount: commands.Range[int, 1] | None = None,  # ty: ignore [invalid-type-form]
    ) -> None:
        """Handle the close position command."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        try:
            (
                ticker,
                pnl_precise,
                total_credit_precise,
                final_credit_int,
                closed_amount,
                is_partial_close,
            ) = await self.trading_logic.close_position(
                user_id=ctx.author.id,
                guild_id=guild_id,
                position_id=position_id,
                close_amount=amount,
            )

            # This is where we show the "slippage fee"
            transaction_fee = total_credit_precise - final_credit_int
            pnl_color = "📈" if pnl_precise >= 0 else "📉"
            title = "✅ **Partial Position Closed**" if is_partial_close else "✅ **Position Closed**"

            response_content = (
                f"{title} {ticker.upper()} (ID: {position_id})\n"
                f"Original Cost Closed: ${abs(closed_amount):,.2f}\n"
                f"{pnl_color} Realized P&L: **${pnl_precise:,.2f}**\n\n"
                f"Total Value: ${total_credit_precise:,.2f}\n"
                f"Transaction Fee: ${transaction_fee:,.2f}\n"
                f"Cash Credited: **${final_credit_int:,.2f}**"
            )

            if is_partial_close:
                response_content += f"\n\nℹ️ *Part of position {position_id} remains open.*"  # noqa: RUF001

            await ctx.send(response_content, ephemeral=True)

        except Exception as e:  # noqa: BLE001
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(
        name="price",
        description="Get the cached prices of all supported stocks.",
    )
    async def price(self, ctx: commands.Context) -> None:
        """Handle the price command, reading all prices from the cache."""
        try:
            # 1. Ensure the cache is fresh for all tickers
            await self.trading_logic.price_cache.get_fresh_prices()

            price_list_lines = []
            last_update_time = None  # To show in the footer

            # 2. Get prices for all supported tickers
            sorted_tickers = sorted(ALLOWED_STOCKS)

            for ticker in sorted_tickers:
                # Get price from the local cache
                price_data = self.trading_logic.price_cache.get_cached_price(ticker)

                if price_data[0] is not None and price_data[1] is not None:
                    current_price, timestamp = price_data
                    price_list_lines.append(f"**{ticker}**: ${current_price:.2f}")
                    if last_update_time is None:  # Grab the first valid timestamp
                        last_update_time = timestamp
                else:
                    price_list_lines.append(f"**{ticker}**: Price N/A")

            # 3. Build the embed
            embed = discord.Embed(
                title="📈 Supported Stock Prices",
                color=discord.Colour.blue(),
                description="\n".join(price_list_lines),
            )

            market_status = "Market is OPEN" if self.trading_logic.is_market_open() else "Market is CLOSED"

            if last_update_time:
                # Format the timestamp relative to the user
                embed.set_footer(
                    text=f"🇺🇸 {market_status} | Prices as of: {format_dt(last_update_time, 'R')}",
                )
            else:
                embed.set_footer(
                    text=f"🇺🇸 {market_status} | Prices are currently unavailable.",
                )

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:  # noqa: BLE001
            # Use the existing error handler
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(
        name="portfolio",
        description="View your paper trading portfolio.",
    )
    async def portfolio(self, ctx: commands.Context) -> None:
        """Display the user's current portfolio value and holdings."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        try:
            portfolio_data = await self.trading_logic.calculate_portfolio_value(
                ctx.author.id,
                guild_id,
            )

            if not portfolio_data:
                await ctx.send("Could not calculate portfolio.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"{ctx.author.display_name}'s Portfolio",
                color=discord.Colour.blue(),
            )
            embed.add_field(
                name="💰 Total Value (Equity)",
                value=f"${portfolio_data['total_value']:,.2f}",
                inline=True,
            )
            embed.add_field(
                name="💵 Cash Balance",
                value=f"${portfolio_data['cash_balance']:,.2f}",
                inline=True,
            )

            total_pnl = portfolio_data["total_pnl"]
            pnl_color = discord.Colour.brand_green() if total_pnl >= 0 else discord.Colour.brand_red()
            embed.add_field(
                name="📈 Total P&L",
                value=f"${total_pnl:+.2f}",
                inline=True,
            )
            embed.color = pnl_color  # Color embed based on overall P&L

            # Add new fields for Long Value and Short Liability
            embed.add_field(
                name="⬆️ Long Holdings Value",
                value=f"${portfolio_data['holdings_value']:,.2f}",
                inline=True,
            )
            embed.add_field(
                name="⬇️ Short Liability",
                value=f"${portfolio_data['short_liability']:,.2f}",
                inline=True,
            )
            embed.add_field(
                name="🔒 Short Collateral",
                value=f"${portfolio_data['short_collateral']:,.2f}",
                inline=True,
            )

            holdings_str = ""
            if portfolio_data["positions"]:
                # Sort by timestamp
                sorted_positions = sorted(
                    portfolio_data["positions"],
                    key=lambda x: x["timestamp"],
                )

                for pos in sorted_positions:
                    pos_id = pos["id"]
                    ticker = pos["ticker"]
                    invested = pos["invested"]  # e.g., 100 or -100
                    entry = pos["entry"]

                    val_str = f"${pos['current_value']:,.2f}" if pos["current_value"] is not None else "N/A"
                    pnl_str = f"${pos['pnl']:+.2f}" if pos["pnl"] is not None else "N/A"

                    # Use sign of invested amount
                    pos_type = "LONG" if invested > 0 else "SHORT"

                    holdings_str += f"**ID: {pos_id}** | **{ticker}** ({pos_type})\n"
                    holdings_str += f"└ Invested: ${invested:,.2f} @ ${entry:,.2f}\n"
                    holdings_str += f"└ Current Val: {val_str} | P&L: {pnl_str}\n"
            else:
                holdings_str = "No open positions. Use /buy or /short to open one."

            if len(holdings_str) > 1024:
                holdings_str = holdings_str[:1020] + "..."

            embed.add_field(
                name="📊 Open Positions (Lots)",
                value=holdings_str,
                inline=False,
            )

            market_status = "Market is OPEN" if self.trading_logic.is_market_open() else "Market is CLOSED"
            embed.set_footer(
                text=f"🇺🇸 {market_status} | Use /close <ID> to close a position.",
            )

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:  # noqa: BLE001
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(
        name="stocks",
        description="List all tradable stocks and their descriptions.",
    )
    async def list_stocks(self, ctx: commands.Context) -> None:
        """Display the list of tradable stocks."""
        embed = discord.Embed(
            title="Tradable Leveraged ETFs",
            description="Here are the currently supported assets for paper trading. All are leveraged ETFs and are intended for short-term trading.",  # noqa: E501
            color=discord.Colour.blurple(),
        )

        embed.add_field(
            name="1. Broad Market Long: TQQQ (NASDAQ)",
            value="TQQQ is a **3× leveraged ETF** that seeks to deliver three times the daily return of the NASDAQ-100 index. It represents a bullish position on large-cap technology and growth stocks. Because of daily compounding, it is generally suited for **short- to medium-term trades**, not long-term holding.",  # noqa: RUF001, E501
            inline=False,
        )
        embed.add_field(
            name="2. Small Cap: TNA",
            value="TNA provides **3× daily exposure** to the Russell 2000 Index, which tracks smaller U.S. companies. Small-cap stocks tend to be more sensitive to economic cycles, making TNA useful for traders expecting **domestic growth acceleration** or a shift toward riskier assets.",  # noqa: RUF001, E501
            inline=False,
        )
        embed.add_field(
            name="3. Sector Long: SOXL (Semiconductors)",
            value="SOXL delivers **3× daily returns** of a semiconductor industry index. This sector underpins much of the modern economy — powering everything from smartphones to AI systems. Traders use SOXL to express a **high-conviction view on tech hardware growth**.",  # noqa: RUF001, E501
            inline=False,
        )
        embed.add_field(
            name="4. Sector Short: FAZ (Financials)",
            value="FAZ provides **–3× daily returns** of an index tracking major U.S. financial institutions. It allows for speculation or hedging against **weakness in the banking or credit sectors**, often used during periods of tightening monetary policy or financial stress.",  # noqa: RUF001, E501
            inline=False,
        )
        embed.add_field(
            name="5. Bonds: TMF",
            value="TMF offers **3× daily exposure** to long-term U.S. Treasury bonds. It typically benefits when **interest rates fall** or investors move toward safe assets. Traders often use TMF as a **diversifier or defensive position** during equity downturns.",  # noqa: RUF001, E501
            inline=False,
        )
        embed.add_field(
            name="6. Gold: UGL",
            value="UGL tracks **2× the daily performance** of gold bullion prices. It serves as a leveraged way to gain exposure to **precious metals as a hedge** against inflation, currency weakness, or market volatility.",  # noqa: RUF001, E501
            inline=False,
        )
        embed.add_field(
            name="7. Bitcoin: BITX",
            value="BITX provides **2× daily exposure** to the price of Bitcoin. It captures the volatility and momentum of the cryptocurrency market, making it suitable for **short-term speculative trades** on digital assets rather than long-term investment.",  # noqa: RUF001, E501
            inline=False,
        )

        embed.set_footer(text="Use /price <ticker> to get the latest cached price.")

        await ctx.send(embed=embed)


# --- Cog Setup Function ---
async def setup(bot: KiwiBot) -> None:
    """Cog setup function called by discord.py."""
    if not bot.trading_logic:
        log.warning("Skipping loading PaperTradingCog: TradingLogic not initialized.")
        return

    await bot.add_cog(PaperTradingCog(bot))
    log.info("PaperTradingCog frontend added to bot.")
