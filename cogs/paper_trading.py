# In cogs/paper_trading.py
from __future__ import annotations  # Defer type annotation evaluation

import logging
from typing import TYPE_CHECKING

# --- Discord Imports ---
import discord
from discord import Interaction, app_commands
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

    # --- Command Groups ---
    stocks = app_commands.Group(name="stocks", description="Commands related to stock information.")

    # --- Helper to ensure guild context ---
    async def _ensure_guild_context(self, ctx: commands.Context) -> GuildId | None:
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.", ephemeral=True)
            return None
        return ctx.guild.id

    # --- Helper for Error Handling ---

    async def _handle_trading_errors(
        self,
        ctx_or_interaction: commands.Context | Interaction,
        error: Exception,
    ) -> None:
        """Reply to common trading errors."""
        ephemeral = True  # Usually want trading errors private
        target = (
            ctx_or_interaction
            if isinstance(ctx_or_interaction, Interaction) and ctx_or_interaction.is_done()
            else (ctx_or_interaction.response if isinstance(ctx_or_interaction, Interaction) else ctx_or_interaction)
        )

        # Add the new error types to the list of "safe" errors
        if isinstance(
            error,
            InsufficientFundsError | PortfolioNotFoundError | PriceNotAvailableError | ValueError,
        ):
            await target.send(f"❌ Error: {error}", ephemeral=ephemeral)
        elif isinstance(error, commands.CommandError):
            log.warning("CommandError during trading operation: %s", error)
            await target.send(f"⚠️ An application error occurred: {error}", ephemeral=ephemeral)
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
    @app_commands.describe(ticker="Symbol (e.g., TQQQ).", quantity="Number of shares.")
    async def buy(self, ctx: commands.Context, ticker: str, quantity: float) -> None:
        """Handle the buy command, calling middleware."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        if ticker.upper() not in ALLOWED_STOCKS:
            supported_tickers = ", ".join(sorted(ALLOWED_STOCKS))
            await ctx.send(
                f"❌ **Invalid Symbol:** `{ticker.upper()}` is not a supported ticker.\n"
                f"Please choose from: `{supported_tickers}`",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await ctx.send("Quantity must be positive.", ephemeral=True)
            return

        try:
            filled_price, new_balance, timestamp = await self.trading_logic.execute_trade(
                user_id=ctx.author.id,
                guild_id=guild_id,
                ticker=ticker,
                quantity=quantity,
                order_type="buy",
            )
            response_content = (
                f"✅ **BOUGHT** {quantity} {ticker.upper()} @ **${filled_price:.2f}**\n(Price as of {format_dt(timestamp, 'R')})\n"
                f"Total Cost: ${quantity * filled_price:.2f}\n"
                f"New Cash Balance: ${new_balance:.2f}"
            )
            await ctx.send(response_content, ephemeral=True)

        except Exception as e:
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(name="sell", description="Sell shares of a stock.")
    @app_commands.describe(ticker="Symbol you own (e.g., AAPL).", quantity="Number of shares.")
    async def sell(self, ctx: commands.Context, ticker: str, quantity: float) -> None:
        """Handle the sell command, calling middleware."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        # --- ADD THIS VALIDATION BLOCK ---
        if ticker.upper() not in ALLOWED_STOCKS:
            supported_tickers = ", ".join(sorted(ALLOWED_STOCKS))
            await ctx.send(
                f"❌ **Invalid Symbol:** `{ticker.upper()}` is not a supported ticker.\n"
                f"Please choose from: `{supported_tickers}`",
                ephemeral=True,
            )
            return
        # --- END VALIDATION BLOCK ---
        if quantity <= 0:
            await ctx.send("Quantity must be positive.", ephemeral=True)
            return

        try:
            filled_price, new_balance, timestamp = await self.trading_logic.execute_trade(
                user_id=ctx.author.id,
                guild_id=guild_id,
                ticker=ticker,
                quantity=quantity,
                order_type="sell",
            )
            response_content = (
                f"✅ **SOLD** {quantity} {ticker.upper()} @ **${filled_price:.2f}**\n(Price as of {format_dt(timestamp, 'R')})\n"
                f"Total Credit: ${quantity * filled_price:.2f}\n"
                f"New Cash Balance: ${new_balance:.2f}"
            )
            await ctx.send(response_content, ephemeral=True)

        except Exception as e:
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(name="price", description="Get the cached prices of all supported stocks.")
    async def price(self, ctx: commands.Context) -> None:
        """Handle the price command, reading all prices from the cache."""
        try:
            # 1. Ensure the cache is fresh for all tickers
            await self.trading_logic.ensure_cache_is_fresh()

            price_list_lines = []
            last_update_time = None  # To show in the footer

            # 2. Get prices for all supported tickers
            sorted_tickers = sorted(ALLOWED_STOCKS)

            for ticker in sorted_tickers:
                # Get price from the local cache
                price_data = await self.trading_logic.get_cached_stock_price(ticker)

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

            if last_update_time:
                # Format the timestamp relative to the user
                embed.set_footer(text=f"Prices as of: {format_dt(last_update_time, 'R')}")
            else:
                embed.set_footer(text="Prices are currently unavailable.")

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            # Use the existing error handler
            await self._handle_trading_errors(ctx, e)

    @commands.hybrid_command(name="portfolio", description="View your paper trading portfolio.")
    async def portfolio(self, ctx: commands.Context) -> None:
        """Display the user's current portfolio value and holdings."""
        guild_id = await self._ensure_guild_context(ctx)
        if not guild_id:
            return

        try:
            portfolio_data = await self.trading_logic.calculate_portfolio_value(ctx.author.id, guild_id)

            if not portfolio_data:
                await ctx.send("Could not calculate portfolio.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"{ctx.author.display_name}'s Portfolio",
                color=discord.Colour.blue(),
            )
            embed.add_field(
                name="💰 Total Value",
                value=f"${portfolio_data['total_value']:.2f}",
                inline=True,
            )
            embed.add_field(
                name="💵 Cash Balance",
                value=f"${portfolio_data['cash_balance']:.2f}",
                inline=True,
            )

            total_pnl = portfolio_data["total_pnl"]
            pnl_color = discord.Colour.brand_green() if total_pnl >= 0 else discord.Colour.brand_red()
            embed.add_field(name="📈 Total P&L", value=f"${total_pnl:+.2f}", inline=True)
            embed.color = pnl_color  # Color embed based on overall P&L

            holdings_str = ""
            if portfolio_data["positions"]:
                for pos in portfolio_data["positions"]:
                    ticker = pos["ticker"]
                    qty = pos["quantity"]
                    mkt_val_str = f"${pos['market_value']:.2f}" if pos["market_value"] is not None else "N/A"
                    pnl_str = f"${pos['pnl']:+.2f}" if pos["pnl"] is not None else "N/A"
                    holdings_str += f"**{ticker}**: {qty} shares | Val: {mkt_val_str} | P&L: {pnl_str}\n"
            else:
                holdings_str = "No current holdings."

            embed.add_field(
                name="📊 Holdings",
                value=holdings_str[:1020] + ("..." if len(holdings_str) > 1024 else ""),
                inline=False,
            )
            # Add footer to explain cached prices
            # embed.set_footer(text=f"Values based on cached prices, refreshed every {self.trading_logic.CACHE_TTL}s.")

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            await self._handle_trading_errors(ctx, e)

    @stocks.command(name="list", description="List all tradable stocks and their descriptions.")
    async def list_stocks(self, interaction: Interaction) -> None:
        """Display the list of tradable stocks."""
        embed = discord.Embed(
            title="Tradable Leveraged ETFs",
            description="Here are the currently supported assets for paper trading. All are leveraged ETFs and are intended for short-term trading.",  # noqa: E501
            color=discord.Colour.blurple(),
        )

        embed.add_field(
            name="1. Broad Market Long: TQQQ (NASDAQ)",
            value="TQQQ is a **3× leveraged ETF** that seeks to deliver three times the daily return of the NASDAQ-100 index. It represents a bullish position on large-cap technology and growth stocks. Because of daily compounding, it is generally suited for **short- to medium-term trades**, not long-term holding.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="2. Broad Market Short: SQQQ (NASDAQ)",
            value="SQQQ is the inverse counterpart to TQQQ, offering **–3× the daily performance** of the NASDAQ-100. It allows traders to profit from or hedge against market declines in major tech-driven indices. Like all leveraged ETFs, it is primarily designed for **short-term tactical positioning**.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="3. Small Cap: TNA",
            value="TNA provides **3× daily exposure** to the Russell 2000 Index, which tracks smaller U.S. companies. Small-cap stocks tend to be more sensitive to economic cycles, making TNA useful for traders expecting **domestic growth acceleration** or a shift toward riskier assets.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="4. Sector Long: SOXL (Semiconductors)",
            value="SOXL delivers **3× daily returns** of a semiconductor industry index. This sector underpins much of the modern economy — powering everything from smartphones to AI systems. Traders use SOXL to express a **high-conviction view on tech hardware growth**.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="5. Sector Short: FAZ (Financials)",
            value="FAZ provides **–3× daily returns** of an index tracking major U.S. financial institutions. It allows for speculation or hedging against **weakness in the banking or credit sectors**, often used during periods of tightening monetary policy or financial stress.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="6. Bonds: TMF",
            value="TMF offers **3× daily exposure** to long-term U.S. Treasury bonds. It typically benefits when **interest rates fall** or investors move toward safe assets. Traders often use TMF as a **diversifier or defensive position** during equity downturns.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="7. Gold: UGL",
            value="UGL tracks **2× the daily performance** of gold bullion prices. It serves as a leveraged way to gain exposure to **precious metals as a hedge** against inflation, currency weakness, or market volatility.",  # noqa: E501
            inline=False,
        )
        embed.add_field(
            name="8. Bitcoin: BITX",
            value="BITX provides **2× daily exposure** to the price of Bitcoin. It captures the volatility and momentum of the cryptocurrency market, making it suitable for **short-term speculative trades** on digital assets rather than long-term investment.",  # noqa: E501
            inline=False,
        )

        embed.set_footer(text="Use /price <ticker> to get the latest cached price.")

        await interaction.send(embed=embed, ephemeral=True)


# --- Cog Setup Function ---
async def setup(bot: KiwiBot) -> None:
    """Cog setup function called by discord.py."""
    if not bot.trading_logic:
        log.warning("Skipping loading PaperTradingCog: TradingLogic not initialized.")
        return

    await bot.add_cog(PaperTradingCog(bot))
    log.info("PaperTradingCog frontend added to bot.")
