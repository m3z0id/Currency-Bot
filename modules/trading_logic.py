# In modules/trading_logic.py
# Using Python 3.13+ features
from __future__ import annotations  # Defer type annotation evaluation

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Final

from modules.types import PositiveInt

# --- Third-Party Imports ---
# Removed: from twelvedata import TDClient
# Removed: from twelvedata.exceptions import BadRequestError, InvalidApiKeyError, TwelveDataError

# --- Local Imports ---
if TYPE_CHECKING:
    from modules.Database import Database
    from modules.types import GuildId, UserId
    from modules.UserDB import UserDB

# Import new exceptions
from modules.aio_twelvedata import AioTwelveDataClient, AioTwelveDataError, AioTwelveDataRequestError
from modules.enums import StatName

# --- Type Hinting Setup ---
type Ticker = str
type Quantity = int | float
type Price = float

# --- Logging ---
log = logging.getLogger(__name__)

# --- Constants ---
# Tier 1: Only allowed stocks given rate limit
ALLOWED_STOCKS: Final[set[Ticker]] = {
    "TQQQ",
    "SQQQ",
    "TNA",
    "SOXL",
    "FAZ",
    "TMF",
    "UGL",
    "BITX",
}
# Time-To-Live for cached prices in seconds
CACHE_TTL: Final[int] = 600  # 10 minutes


# --- Custom Exceptions (can be shared or defined here) ---
class InsufficientFundsError(Exception):
    """Raised when a user doesn't have enough cash for a transaction."""


class PortfolioNotFoundError(Exception):
    """Raised when a user's portfolio doesn't exist yet."""


class PriceNotAvailableError(Exception):
    """Raised when a known ticker's price is not in the cache (e.g., pending update)."""


# --- Middleware Class ---
class TradingLogic:
    """Handles interactions with the trading data API and database.
    Manages a local cache of prices and a set of known tickers to
    respect API rate limits.
    """

    TABLE_NAME: str = "user_holdings"

    def __init__(
        self,
        database: Database,
        user_db: UserDB,
        api_key: str,  # Use the new client
    ) -> None:
        self.database = database
        self.user_db = user_db
        self.api_client = AioTwelveDataClient(api_key=api_key)
        log.info("TradingLogic initialized with AioTwelveDataClient.")

        # --- Caching & State ---
        self.global_price_cache: dict[Ticker, Price] = {}
        self.global_price_cache_timestamps: dict[Ticker, datetime.datetime] = {}
        self.last_cache_refresh_time: datetime.datetime = datetime.datetime.min.replace(
            tzinfo=datetime.UTC,
        )

        # --- Concurrency Locks ---
        # Protects concurrent access to the ticker set and price caches
        self.cache_lock = asyncio.Lock()
        # Simple lock to ensure only one user-facing API call (lookup) happens at a time
        self.api_rate_limit_lock = asyncio.Lock()

    async def post_init(self) -> None:
        """Initialize the database table for portfolios."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                    user_id     INTEGER CHECK(user_id > 1000000),
                    guild_id    INTEGER CHECK(guild_id > 1000000),
                    ticker      TEXT NOT NULL,
                    quantity    REAL NOT NULL,
                    avg_cost    REAL NOT NULL,
                    PRIMARY KEY (user_id, guild_id, ticker),
                    FOREIGN KEY (user_id, guild_id) REFERENCES users(discord_id, guild_id),
                    CHECK(quantity > 0)
                ) STRICT, WITHOUT ROWID;
                """,
            )
            await conn.commit()
            log.info("Initialized user_holdings database table.")

    # --- Database Helpers ---
    async def fetch_holdings(self, user_id: UserId, guild_id: GuildId) -> list[dict]:
        """Retrieve all of a user's holdings from the database."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"SELECT ticker, quantity, avg_cost FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            rows = await cursor.fetchall()

        return [
            {
                "ticker": row[0],
                "quantity": row[1],
                "avg_cost": row[2],
            }
            for row in rows
        ]

    # --- NEW: Cache & API Wrappers ---

    async def get_cached_stock_price(
        self,
        ticker: Ticker,
    ) -> tuple[Price, datetime.datetime] | tuple[None, None]:
        """Fetches the latest price for a ticker *from the local cache*.
        Does NOT make an API call.
        """
        ticker = ticker.upper()
        async with self.cache_lock:
            price = self.global_price_cache.get(ticker)
            timestamp = self.global_price_cache_timestamps.get(ticker)

        if price is not None and timestamp is not None:
            # The timestamp here is for the individual ticker, which is fine.
            # The global refresh time is what triggers the update.
            return price, timestamp
        return None, None

    async def ensure_cache_is_fresh(self) -> None:
        """Gatekeeper method. Ensures the price cache is fresh (<= 10 min old) before
        allowing any price-dependent operation to proceed. This is the only method
        that makes API calls.
        """
        now = datetime.datetime.now(datetime.UTC)
        is_stale = (now - self.last_cache_refresh_time).total_seconds() > CACHE_TTL

        if not is_stale:
            return

        log.info("Global price cache is stale. Acquiring lock for batch refresh...")
        async with self.api_rate_limit_lock:
            # Double-check staleness inside the lock to prevent stampedes
            now = datetime.datetime.now(datetime.UTC)
            if (now - self.last_cache_refresh_time).total_seconds() <= CACHE_TTL:
                log.info("Cache was refreshed by another task. Proceeding.")
                return

            # We hold the lock and the cache is confirmed stale. Refresh all 8 tickers.
            log.info(
                "Performing full batch refresh for %d tickers...",
                len(ALLOWED_STOCKS),
            )
            try:
                # Directly use the ALLOWED_STOCKS constant for the API call
                price_map = await self.api_client.get_batch_prices(ALLOWED_STOCKS)
                update_time = datetime.datetime.now(datetime.UTC)

                async with self.cache_lock:
                    for ticker, price in price_map.items():
                        self.global_price_cache[ticker.upper()] = price
                        self.global_price_cache_timestamps[ticker.upper()] = update_time

                self.last_cache_refresh_time = update_time
                log.info("Full batch refresh complete.")

            except (AioTwelveDataError, AioTwelveDataRequestError) as e:
                log.exception("Batch refresh API error: %s", e)
                msg = "Could not refresh prices. The data provider may be unavailable."
                raise ConnectionError(msg) from e

    # --- Financial Engine / Simulation Logic ---
    async def execute_trade(
        self,
        user_id: UserId,
        guild_id: GuildId,
        ticker: Ticker,
        quantity: Quantity,
        order_type: str,
    ) -> tuple[Price, Price, datetime.datetime]:
        """Handles the logic for executing a buy or sell order using cached prices."""
        ticker = ticker.upper()

        # Gatekeeper: Ensure cache is fresh before proceeding.
        await self.ensure_cache_is_fresh()

        # --- NEW: Price & Ticker Validation ---
        # 1. Get price from cache. After the gatekeeper, we know it's fresh.
        current_price, timestamp = await self.get_cached_stock_price(ticker)

        if current_price is None:
            # This now means the ticker is invalid, delisted, or was just added
            # via /lookup and the subsequent refresh failed to find it.
            log.warning(
                "Trade rejected: User %s tried to trade %s, but its price is not in the cache.",
                user_id,
                ticker,
            )
            msg = f"Trade rejected: {ticker} is not a supported trading symbol."
            raise PriceNotAvailableError(msg)

        log.info(
            "Executing trade for %s @ cached price $%.2f (from %s)",
            ticker,
            current_price,
            timestamp,
        )
        cost = PositiveInt(int(quantity * current_price))
        # --- End Price Validation ---

        # Get a single connection for the entire transaction
        async with self.database.get_conn() as conn:
            try:
                if order_type == "buy":
                    # 1. Decrement cash using UserDB's encapsulated method
                    new_balance_val = await self.user_db.decrement_stat(
                        user_id,
                        guild_id,
                        StatName.CURRENCY,
                        cost,
                        conn=conn,
                    )

                    if new_balance_val is None:
                        # The decrement failed (insufficient funds)
                        current_cash = await self.user_db.get_stat(
                            user_id,
                            guild_id,
                            StatName.CURRENCY,
                        )
                        msg = f"Insufficient funds. Needs ${cost:.2f}, has ${current_cash:.2f}."
                        raise InsufficientFundsError(msg)

                    # 2. Get current holding (logic for its own table)
                    cursor = await conn.execute(
                        f"SELECT quantity, avg_cost FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ? AND ticker = ?",
                        (user_id, guild_id, ticker),
                    )
                    row = await cursor.fetchone()

                    if row:
                        current_qty, current_avg_cost = row
                        new_qty = current_qty + quantity
                        new_avg_cost = ((current_avg_cost * current_qty) + cost) / new_qty
                    else:
                        new_qty = quantity
                        new_avg_cost = current_price

                    # 3. UPSERT the 'user_holdings' table (logic for its own table)
                    await conn.execute(
                        f"""INSERT INTO {self.TABLE_NAME} (user_id, guild_id, ticker, quantity, avg_cost)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(user_id, guild_id, ticker) DO UPDATE SET
                           quantity = ?, avg_cost = ?
                        """,
                        (
                            user_id,
                            guild_id,
                            ticker,
                            new_qty,
                            new_avg_cost,
                            new_qty,
                            new_avg_cost,
                        ),
                    )

                elif order_type == "sell":
                    # 1. Get current holding to validate sale
                    cursor = await conn.execute(
                        f"SELECT quantity FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ? AND ticker = ?",
                        (user_id, guild_id, ticker),
                    )
                    row = await cursor.fetchone()

                    current_quantity = row[0] if row else 0.0
                    if not row or current_quantity < quantity:
                        msg = f"Cannot sell {quantity} shares of {ticker}. Only owns {current_quantity}."
                        raise ValueError(msg)

                    new_qty = current_quantity - quantity

                    # 2. Increment cash using UserDB's encapsulated method
                    new_balance_val = await self.user_db.increment_stat(
                        user_id,
                        guild_id,
                        StatName.CURRENCY,
                        cost,
                        conn=conn,
                    )

                    # 3. Update or Delete from 'user_holdings' table
                    if abs(new_qty) < 1e-3:  # Check for near-zero float
                        await conn.execute(
                            f"DELETE FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ? AND ticker = ?",
                            (user_id, guild_id, ticker),
                        )
                    else:
                        await conn.execute(
                            f"UPDATE {self.TABLE_NAME} SET quantity = ? WHERE user_id = ? AND guild_id = ? AND ticker = ?",
                            (new_qty, user_id, guild_id, ticker),
                        )
                else:
                    msg = "Invalid order_type specified."
                    raise ValueError(msg)

                # 4. Commit the single, atomic transaction
                await conn.commit()
                log.info(
                    "Trade committed for user %s: %s %s %s @ $%.2f",
                    user_id,
                    order_type.upper(),
                    quantity,
                    ticker,
                    current_price,
                )

                # 5. Get the final cash balance to return it
                # Note: new_balance_val is already the correct new balance
                return current_price, new_balance_val, timestamp

            except Exception:
                await conn.rollback()
                log.exception("Trade failed and was rolled back for user %s", user_id)
                raise

    # --- Other Business Logic (e.g., portfolio P&L calculation) ---
    async def calculate_portfolio_value(
        self,
        user_id: UserId,
        guild_id: GuildId,
    ) -> dict | None:
        """Calculates the current market value and P&L of a user's portfolio
        using ONLY cached prices.
        """
        await self.ensure_cache_is_fresh()
        holdings = await self.fetch_holdings(user_id, guild_id)

        # Fetch cash from UserDB, the single source of truth
        user_cash_balance = await self.user_db.get_stat(
            user_id,
            guild_id,
            StatName.CURRENCY,
        )

        total_market_value = 0.0
        total_cost_basis = 0.0
        positions = []

        # This loop is now fast and makes NO API calls
        for holding in holdings:
            ticker, data = holding["ticker"], holding
            quantity = data["quantity"]
            avg_cost = data["avg_cost"]
            cost_basis = quantity * avg_cost
            total_cost_basis += cost_basis

            # Get price from cache
            current_price, _ = await self.get_cached_stock_price(ticker)

            if current_price is not None:
                market_value = quantity * current_price
                pnl = market_value - cost_basis
                total_market_value += market_value

                positions.append(
                    {
                        "ticker": ticker,
                        "quantity": quantity,
                        "avg_cost": avg_cost,
                        "current_price": current_price,
                        "market_value": market_value,
                        "pnl": pnl,
                        "cost_basis": cost_basis,
                    },
                )
            else:
                # Price fetch failed or is pending (e.g., delisted stock)
                log.warning(
                    "Could not get cached price for owned ticker %s in user %s portfolio.",
                    ticker,
                    user_id,
                )
                positions.append(
                    {
                        "ticker": ticker,
                        "quantity": data["quantity"],
                        "avg_cost": data["avg_cost"],
                        "current_price": None,  # Indicate price fetch failed
                        "market_value": None,
                        "pnl": None,
                        "cost_basis": cost_basis,  # Cost basis is still known
                    },
                )

        total_pnl = total_market_value - total_cost_basis
        total_portfolio_value = user_cash_balance + total_market_value

        return {
            "cash_balance": user_cash_balance,
            "holdings_value": total_market_value,
            "total_value": total_portfolio_value,
            "total_pnl": total_pnl,
            "positions": positions,
        }
