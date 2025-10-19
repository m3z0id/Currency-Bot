# In modules/trading_logic.py
from __future__ import annotations  # Defer type annotation evaluation

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from modules.types import PositiveInt

# --- Local Imports ---
if TYPE_CHECKING:
    from aiosqlite import Connection

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
NY_TZ: Final[ZoneInfo] = ZoneInfo("America/New_York")


class PriceCache:
    """Manages fetching and caching of stock prices with a single lock."""

    def __init__(self, api_client: AioTwelveDataClient) -> None:
        self.api_client = api_client
        self._lock = asyncio.Lock()
        self._prices: dict[Ticker, Price] = {}
        self._timestamps: dict[Ticker, datetime.datetime] = {}
        self._last_refresh = datetime.datetime.min.replace(tzinfo=datetime.UTC)

    def is_us_market_open(self) -> bool:
        """Check if the US market is (conservatively) open."""
        # Get the current time in New York
        now_et = datetime.datetime.now(NY_TZ)

        # 1. Check for weekends (Monday=0, Sunday=6)
        if now_et.weekday() >= 5:
            return False  # It's Saturday or Sunday

        # 2. Define market hours in ET
        market_open_time = datetime.time(9, 30)
        market_close_time = datetime.time(16, 0)

        # 3. Check if current time is within market hours
        # This conservatively ignores pre-market/after-hours
        return market_open_time <= now_et.time() <= market_close_time

    async def get_fresh_prices(self) -> dict[Ticker, Price]:
        """Return a dictionary of fresh prices, refreshing from the API if stale."""
        async with self._lock:
            now_utc = datetime.datetime.now(datetime.UTC)  # Use UTC for comparison

            # 1. Check all conditions
            is_stale = (now_utc - self._last_refresh).total_seconds() > CACHE_TTL
            is_empty = not self._prices
            market_is_open = self.is_us_market_open()

            # 2. Decide whether to refresh
            # REFRESH IF:
            #   (A) The bot just started (cache is empty)
            #   (B) The market is open AND our cache TTL has expired
            if is_empty or (market_is_open and is_stale):
                log.info(
                    "Refreshing price cache. Reason: %s",
                    "Cache is empty" if is_empty else "Cache is stale and market is open",
                )

                try:
                    price_map = await self.api_client.get_batch_prices(ALLOWED_STOCKS)
                    update_time = datetime.datetime.now(datetime.UTC)

                    for ticker, price in price_map.items():
                        if price is not None:
                            self._prices[ticker.upper()] = price
                            self._timestamps[ticker.upper()] = update_time

                    self._last_refresh = update_time
                    log.info("Full batch refresh complete.")

                except (AioTwelveDataError, AioTwelveDataRequestError) as e:
                    log.exception("Batch refresh API error")
                    msg = "Could not refresh prices. The data provider may be unavailable."
                    # If the cache is empty, we must raise. Otherwise, we can
                    # serve stale data to avoid failing all /price commands.
                    if is_empty:
                        raise ConnectionError(msg) from e
                    log.warning("Serving stale prices due to API error.")

            # 3. Always return the current cache state
            return self._prices

    def get_cached_price(self, ticker: Ticker) -> tuple[Price, datetime.datetime] | tuple[None, None]:
        """Get a single price and its timestamp directly from the cache."""
        return self._prices.get(ticker), self._timestamps.get(ticker)


# --- Custom Exceptions (can be shared or defined here) ---
class InsufficientFundsError(Exception):
    """Raised when a user doesn't have enough cash for a transaction."""


class PortfolioNotFoundError(Exception):
    """Raised when a user's portfolio doesn't exist yet."""


class PriceNotAvailableError(Exception):
    """Raised when a known ticker's price is not in the cache (e.g., pending update)."""


# --- Middleware Class ---
class TradingLogic:
    """Handle interactions with the trading data API and database.

    Manage a local cache of prices and a set of known tickers to
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
        self.price_cache = PriceCache(self.api_client)
        log.info("TradingLogic initialized with PriceCache and AioTwelveDataClient.")

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
                f"SELECT ticker, quantity, avg_cost FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ?",  # noqa: S608
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

    # --- Financial Engine / Simulation Logic ---
    async def _execute_buy_order(
        self,
        conn: Connection,
        user_id: UserId,
        guild_id: GuildId,
        ticker: Ticker,
        amount: PositiveInt,
        current_price: Price,
    ) -> None:
        """Execute the database operations for a buy order. (Must be run inside a transaction)."""
        cost = amount
        # Round the new shares to 2 decimal places
        new_shares = round(cost / current_price, 2)

        # 1. Atomically decrement cash and check for sufficient funds
        cursor = await conn.execute(
            f"""UPDATE {self.user_db.USERS_TABLE} SET currency = currency - ?
WHERE discord_id = ? AND guild_id = ? AND currency >= ?""",  # noqa: S608
            (cost, user_id, guild_id, cost),
        )
        if cursor.rowcount == 0:
            msg = f"Insufficient funds. You need ${cost:.2f} to make this purchase."
            raise InsufficientFundsError(msg)

        # 2. Fetch existing holding to calculate new average cost in Python
        cursor = await conn.execute(
            f"SELECT quantity, avg_cost FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ? AND ticker = ?",  # noqa: S608
            (user_id, guild_id, ticker),
        )
        row = await cursor.fetchone()
        old_qty, old_avg_cost = (row[0], row[1]) if row else (0.0, 0.0)

        # 3. Calculate new average cost in Python
        total_cost = (old_avg_cost * old_qty) + (current_price * new_shares)
        total_shares = old_qty + new_shares
        # Explicitly round the final total shares
        total_shares = round(total_shares, 2)
        new_avg_cost = total_cost / total_shares if total_shares > 0 else 0.0

        # 4. UPSERT the holding with the new calculated values
        await conn.execute(
            f"""INSERT INTO {self.TABLE_NAME} (user_id, guild_id, ticker, quantity, avg_cost)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(user_id, guild_id, ticker) DO UPDATE SET
                               quantity = ?,
                               avg_cost = ?
                        """,  # noqa: S608
            (
                user_id,
                guild_id,
                ticker,
                total_shares,
                new_avg_cost,
                total_shares,  # For the UPDATE part of the UPSERT
                new_avg_cost,  # For the UPDATE part of the UPSERT
            ),
        )

    async def _execute_sell_order(
        self,
        conn: Connection,
        user_id: UserId,
        guild_id: GuildId,
        ticker: Ticker,
        quantity: Quantity,
        current_price: Price,
    ) -> None:
        """Execute the database operations for a sell order. (Must be run inside a transaction)."""
        credit = PositiveInt(int(quantity * current_price))

        # 1. Get current holding to validate sale
        cursor = await conn.execute(
            f"SELECT quantity FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ? AND ticker = ?",  # noqa: S608
            (user_id, guild_id, ticker),
        )
        row = await cursor.fetchone()

        current_quantity = row[0] if row else 0.0
        if not row or current_quantity < quantity:
            msg = f"Cannot sell {quantity} shares of {ticker}. Only owns {current_quantity}."
            raise ValueError(msg)

        # 2. Update or Delete from 'user_holdings' table
        new_qty = round(current_quantity - quantity, 2)
        if new_qty < 0.01:  # Check if quantity is less than one cent's worth of a share
            await conn.execute(
                f"DELETE FROM {self.TABLE_NAME} WHERE user_id = ? AND guild_id = ? AND ticker = ?",  # noqa: S608
                (user_id, guild_id, ticker),
            )
        else:
            await conn.execute(
                f"UPDATE {self.TABLE_NAME} SET quantity = ? WHERE user_id = ? AND guild_id = ? AND ticker = ?",  # noqa: S608
                (new_qty, user_id, guild_id, ticker),
            )
        # 3. Increment cash
        await conn.execute(
            f"UPDATE {self.user_db.USERS_TABLE} SET currency = currency + ? WHERE discord_id = ? AND guild_id = ?",  # noqa: S608
            (credit, user_id, guild_id),
        )

    async def execute_trade(
        self,
        user_id: UserId,
        guild_id: GuildId,
        ticker: Ticker,
        order_type: str,
        *,
        quantity: Quantity | None = None,
        amount: PositiveInt | None = None,
    ) -> tuple[Price, Price, datetime.datetime]:
        """Execute a buy or sell order using cached prices."""
        ticker = ticker.upper()

        # 1. Get fresh prices from the encapsulated cache
        price_map = await self.price_cache.get_fresh_prices()
        current_price, timestamp = (
            price_map.get(ticker),
            self.price_cache.get_cached_price(ticker)[1],
        )

        if current_price is None:
            # This now means the ticker is invalid, delisted, or the refresh failed.
            # via /lookup and the subsequent refresh failed to find it.
            log.warning(
                "Trade rejected: User %s tried to trade %s, but its price is not in the cache.",
                user_id,
                ticker,
            )
            msg = f"Trade rejected: {ticker} is not a supported trading symbol."
            raise PriceNotAvailableError(msg)

        # Get a single connection for the entire transaction
        async with self.database.get_conn() as conn:
            try:
                # 2. Dispatch to the correct helper
                if order_type == "buy":
                    if amount is None:
                        msg = "Buy orders must specify an amount."
                        raise ValueError(msg)
                    await self._execute_buy_order(conn, user_id, guild_id, ticker, amount, current_price)
                elif order_type == "sell":
                    if quantity is None:
                        msg = "Sell orders must specify a quantity."
                        raise ValueError(msg)
                    await self._execute_sell_order(conn, user_id, guild_id, ticker, quantity, current_price)
                else:
                    msg = "Invalid order_type specified."
                    raise ValueError(msg)

                # 3. Commit the single, atomic transaction
                await conn.commit()
                log.info(
                    "Trade committed for user %s: %s %s %s @ $%.2f",
                    user_id,
                    order_type.upper(),
                    quantity if order_type == "sell" else amount,  # Log amount for buy, qty for sell
                    ticker,
                    current_price,
                )

                # 4. Get the final cash balance to return it
                cursor = await conn.execute(
                    f"SELECT currency FROM {self.user_db.USERS_TABLE} WHERE discord_id = ? AND guild_id = ?",  # noqa: S608
                    (user_id, guild_id),
                )
                new_balance_row = await cursor.fetchone()
                return (
                    current_price,
                    new_balance_row[0] if new_balance_row else 0,
                    timestamp,
                )

            except Exception:
                await conn.rollback()
                log.exception("Trade failed and was rolled back for user %s", user_id)
                raise

    # --- Other Business Logic (e.g., portfolio P&L calculation) ---
    def is_market_open(self) -> bool:
        """Pass-through check to see if the US market is currently open."""
        return self.price_cache.is_us_market_open()

    async def calculate_portfolio_value(
        self,
        user_id: UserId,
        guild_id: GuildId,
    ) -> dict | None:
        """Calculate the current cached market value and P&L of a user's portfolio."""
        # This ensures the cache is fresh before we read from it.
        await self.price_cache.get_fresh_prices()

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
            current_price, _ = self.price_cache.get_cached_price(ticker)

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
