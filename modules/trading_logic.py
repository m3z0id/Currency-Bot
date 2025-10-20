"""Backend for handling trades.

There's an intentional design decision.
UserDB manages currency in dollars. But stock prices are in cents.
We restrict stock quantities to two decimal places.
And the rounding error of quantity * stock price is the slippage.
We communicate this slippage as a "transaction fee".
(While misleading it doubles as a subtle money sink.)
"""

from __future__ import annotations  # Defer type annotation evaluation

import asyncio
import datetime
import logging
import math
from typing import TYPE_CHECKING, Any, Final, Literal

# --- Local Imports ---
if TYPE_CHECKING:
    import aiosqlite

    from modules.Database import Database
    from modules.types import GuildId, UserId
    from modules.UserDB import UserDB

# Import new exceptions
from modules.aio_twelvedata import AioTwelveDataClient, AioTwelveDataError, AioTwelveDataRequestError
from modules.enums import StatName

# --- Type Hinting Setup ---
type Ticker = str

# --- Logging ---
log = logging.getLogger(__name__)


# --- Constants ---
# Tier 1: Only allowed stocks given rate limit
ALLOWED_STOCKS: Final[set[Ticker]] = {
    "TQQQ",
    "TNA",
    "SOXL",
    "FAZ",
    "TMF",
    "UGL",
    "BITX",
}
# Time-To-Live for cached prices in seconds
CACHE_TTL: Final[int] = 300  # 5 minutes


def _parse_api_time(time_str: str) -> datetime.timedelta | None:
    """Parse 'HH:MM:SS' into a timedelta."""
    if not time_str or time_str == "00:00:00":
        return None

    try:
        # Use strptime to parse the time string
        t = datetime.datetime.strptime(time_str, "%H:%M:%S").time()  # noqa: DTZ007
        # Convert the time object to a timedelta
        return datetime.timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)
    except ValueError:
        # This catches any format mismatches
        log.warning("Could not parse API time string: %s", time_str)
        return None


class PriceCache:
    """Manage fetching and caching of stock prices on-demand.

    Using an intelligent, market-aware TTL.
    """

    def __init__(self, api_client: AioTwelveDataClient) -> None:
        self.api_client = api_client
        self._lock = asyncio.Lock()  # Protects against simultaneous refreshes
        # Store prices as Decimals
        self._prices: dict[Ticker, float] = {}
        self._timestamps: dict[Ticker, datetime.datetime] = {}

        # Refresh with an "intelligent TTL"
        self._next_check_time = datetime.datetime.min.replace(tzinfo=datetime.UTC)

        # Caches the *actual* market state from the API
        self._market_state: dict[str, Any] | None = None

    def is_us_market_open(self) -> bool:
        """Check the *cached* market state. This is a synchronous, 0-cost check of the last known state."""
        if not self._market_state:
            return False  # Default to closed if we've never checked
        return self._market_state.get("is_market_open", False)

    async def get_fresh_prices(self) -> dict[Ticker, float]:  # noqa: PLR0912 PLR0915
        """Return a dictionary of fresh prices.

        Refreshing from the API *only* if the intelligent TTL has expired.
        """
        async with self._lock:
            now_utc = datetime.datetime.now(datetime.UTC)
            is_empty = not self._prices

            # 1. Fast Path: Serve cache if our intelligent TTL hasn't expired
            if now_utc < self._next_check_time and not is_empty:
                log.debug(
                    "Serving cached prices; next check scheduled for %s",
                    self._next_check_time,
                )
                return self._prices

            # 2. Slow Path: Cache is stale or empty. We MUST check market state.
            log.info(
                "Triggering cache refresh. Reason: %s",
                "Cache is empty" if is_empty else "Intelligent TTL expired",
            )

            # --- API Call 1: Market State (Uses 1 API credit) ---
            market_state = await self.api_client.get_market_state("NASDAQ")

            if market_state is None:
                # API call failed.
                log.error("Failed to fetch market state. Cannot refresh prices.")
                # Set a short retry window
                self._next_check_time = now_utc + datetime.timedelta(minutes=1)
                if is_empty:
                    msg = "Failed to fetch initial market state. Bot cannot get prices."
                    raise ConnectionError(msg)
                # Serve stale data as a fallback
                log.warning("Serving stale prices due to market state API failure.")
                return self._prices

            # Save the new state
            self._market_state = market_state
            is_open = self._market_state.get("is_market_open", False)

            # 3. Decide action based on market state
            if is_open:
                # --- MARKET IS OPEN ---
                # We must refresh prices
                log.info("Market is OPEN. Refreshing batch prices.")
                # --- API Call 2: Batch Prices (Uses 7 API credits) ---
                try:
                    price_map = await self.api_client.get_batch_prices(ALLOWED_STOCKS)
                    update_time = datetime.datetime.now(datetime.UTC)

                    for ticker, price_float in price_map.items():
                        if price_float is not None:
                            self._prices[ticker.upper()] = price_float
                            self._timestamps[ticker.upper()] = update_time
                    log.info("Full batch refresh complete.")

                    # Set the standard 5-minute TTL
                    self._next_check_time = update_time + datetime.timedelta(
                        seconds=CACHE_TTL,
                    )

                except (AioTwelveDataError, AioTwelveDataRequestError) as e:
                    log.exception("Batch refresh API error")
                    # Set a short retry window
                    self._next_check_time = now_utc + datetime.timedelta(minutes=1)
                    if is_empty:
                        msg = "Could not refresh prices. The data provider may be unavailable."
                        raise ConnectionError(msg) from e
                    log.warning("Serving stale prices due to batch price API error.")

            else:
                # --- MARKET IS CLOSED ---
                log.info("Market is CLOSED. Serving existing prices.")

                # If cache is empty (cold start), we must fetch prices once
                if is_empty:
                    log.info(
                        "Cold start: Fetching initial prices while market is closed.",
                    )
                    try:
                        # --- API Call 2 (Cold Start): Batch Prices (7 credits) ---
                        price_map = await self.api_client.get_batch_prices(
                            ALLOWED_STOCKS,
                        )
                        update_time = datetime.datetime.now(datetime.UTC)
                        for ticker, price_float in price_map.items():
                            if price_float is not None:
                                self._prices[ticker.upper()] = price_float
                                self._timestamps[ticker.upper()] = update_time
                    except Exception:
                        log.exception(
                            "Failed to get *initial* prices while market closed.",
                        )
                        # Set a short retry and return empty dict
                        self._next_check_time = now_utc + datetime.timedelta(minutes=1)
                        return self._prices  # Returns empty {}

                # Now, set the "intelligent TTL"
                time_to_open_str = self._market_state.get("time_to_open")
                time_to_open_delta = _parse_api_time(time_to_open_str)

                if time_to_open_delta:
                    # We have a valid time! Schedule the next check.
                    # Add a 15-second buffer to ensure market is *really* open.
                    buffer = datetime.timedelta(seconds=15)
                    self._next_check_time = now_utc + time_to_open_delta + buffer
                    log.info(
                        "Next market check scheduled for %s (in %s)",
                        self._next_check_time,
                        time_to_open_delta + buffer,
                    )
                else:
                    # API gave no 'time_to_open' (e.g., "00:00:00" on a weekend)
                    # Fall back to a long poll (e.g., 1 hour).
                    log.warning(
                        "No 'time_to_open' provided. Falling back to 1-hour poll.",
                    )
                    self._next_check_time = now_utc + datetime.timedelta(hours=1)

            # 4. Always return the current cache state
            return self._prices

    def get_cached_price(
        self,
        ticker: Ticker,
    ) -> tuple[float, datetime.datetime] | tuple[None, None]:
        """Get a single price and its timestamp directly from the cache."""
        # This method is unchanged
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
                """
                CREATE TABLE IF NOT EXISTS positions (
                    position_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    guild_id        INTEGER NOT NULL,
                    ticker          TEXT NOT NULL,
                    invested_dollars INTEGER NOT NULL, -- User's integer input, e.g., 100 or -100
                    entry_price     REAL NOT NULL,    -- Price at open
                    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                    FOREIGN KEY (user_id, guild_id) REFERENCES users(discord_id, guild_id)
                ) STRICT;
                """,
            )
            await conn.commit()
            log.info("Initialized positions database table.")

    async def _update_cash_balance(
        self,
        conn: aiosqlite.Connection,  # The transaction connection
        user_id: UserId,
        guild_id: GuildId,
        cash_change_int: int,
    ) -> int:
        """Atomically update a user's cash balance.

        Raises InsufficientFundsError if a debit fails.
        Returns the new cash balance (as a Decimal).
        """
        if cash_change_int > 0:
            # A. Credit Cash (Safe)
            await conn.execute(
                f"""INSERT INTO {self.user_db.USERS_TABLE} (discord_id, guild_id, currency) VALUES (?, ?, ?)
                     ON CONFLICT(discord_id, guild_id) DO UPDATE SET currency = currency + ?""",  # noqa: S608
                (user_id, guild_id, cash_change_int, cash_change_int),
            )
        elif cash_change_int < 0:
            # B. Debit Cash (Atomic Check)
            debit_amount = abs(cash_change_int)
            cursor = await conn.execute(
                f"""UPDATE {self.user_db.USERS_TABLE}
                     SET currency = currency - ?
                     WHERE discord_id = ? AND guild_id = ? AND currency >= ?""",  # noqa: S608
                (debit_amount, user_id, guild_id, debit_amount),
            )
            if cursor.rowcount == 0:
                msg = f"Insufficient funds. You need ${debit_amount} to execute this trade."
                raise InsufficientFundsError(msg)

        # C. Get the new balance *after* the update
        cursor = await conn.execute(
            f"SELECT currency FROM {self.user_db.USERS_TABLE} WHERE discord_id = ? AND guild_id = ?",  # noqa: S608
            (user_id, guild_id),
        )
        return (await cursor.fetchone())[0]

    async def open_position(
        self,
        user_id: UserId,
        guild_id: GuildId,
        ticker: Ticker,
        trade_type: Literal["BUY", "SHORT"],
        dollar_amount: int,  # This is now a simple int
    ) -> tuple[float, int, str, datetime.datetime, bool, int]:
        """Open a new long or short position (a new 'lot').

        This is a 'lossless' open, where the user's bank is debited
        by exactly the amount they specified.

        Returns:
            A tuple of (filled_price, invested_dollars_this_trade, action_string,
            timestamp, was_stacked, total_invested_in_position).

        """
        ticker = ticker.upper()

        # 1. Get Price
        price_map = await self.price_cache.get_fresh_prices()
        current_price, timestamp = (
            price_map.get(ticker),
            self.price_cache.get_cached_price(ticker)[1],
        )

        if current_price is None or float(current_price) <= 0:
            msg = f"Trade rejected: {ticker} is not a supported trading symbol or has no price."
            raise PriceNotAvailableError(msg)

        current_price_float = float(current_price)

        # 2. Determine Position Details
        if trade_type == "BUY":
            invested_dollars_int = dollar_amount
            cash_change_int = -dollar_amount  # Debit user
            action = "BOUGHT"
        else:  # "SHORT"
            invested_dollars_int = -dollar_amount
            cash_change_int = -dollar_amount  # Debit for collateral
            action = "SHORTED"

        async with self.database.get_conn() as conn:
            try:
                # 1. Update Cash (raises InsufficientFundsError)
                await self._update_cash_balance(
                    conn,
                    user_id,
                    guild_id,
                    cash_change_int,
                )

                # 2. Check for a stackable position OF THE SAME TYPE
                # We must only stack longs on longs (invested_dollars > 0)
                # and shorts on shorts (invested_dollars < 0)
                stack_direction_check = "invested_dollars > 0" if trade_type == "BUY" else "invested_dollars < 0"

                cursor = await conn.execute(
                    f"""SELECT position_id, invested_dollars FROM positions
                        WHERE user_id = ? AND guild_id = ? AND ticker = ?
                        AND entry_price = ? AND {stack_direction_check}""",  # noqa: S608 (f-string is safe)
                    (user_id, guild_id, ticker, current_price_float),
                )
                existing_position = await cursor.fetchone()

                was_stacked = False
                total_invested_in_position = 0

                if existing_position:
                    # Stack the position
                    was_stacked = True
                    position_id, existing_invested = existing_position
                    new_invested_dollars = existing_invested + invested_dollars_int
                    total_invested_in_position = new_invested_dollars

                    await conn.execute(
                        "UPDATE positions SET invested_dollars = ? WHERE position_id = ?",
                        (new_invested_dollars, position_id),
                    )
                else:
                    # Create a new position lot
                    was_stacked = False
                    total_invested_in_position = invested_dollars_int
                    await conn.execute(
                        """
                        INSERT INTO positions (user_id, guild_id, ticker, invested_dollars, entry_price)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            guild_id,
                            ticker,
                            invested_dollars_int,
                            current_price_float,
                        ),
                    )

                # 3. Commit
                await conn.commit()

                log.info(
                    "Position %s for user %s: %s $%s of %s @ $%s",
                    "stacked" if was_stacked else "opened",
                    user_id,
                    action,
                    dollar_amount,
                    ticker,
                    current_price_float,
                )

            except Exception:
                await conn.rollback()
                log.exception(
                    "Position open failed and was rolled back for user %s",
                    user_id,
                )
                raise
            else:
                return (
                    current_price_float,
                    invested_dollars_int,  # The amount for this trade, e.g., 100 or -100
                    action,
                    timestamp,
                    was_stacked,
                    total_invested_in_position,
                )

    async def close_position(
        self,
        user_id: UserId,
        guild_id: GuildId,
        position_id: int,
        close_amount: int | None = None,
    ) -> tuple[str, float, float, int, int, bool]:
        """Close a position ('lot') fully.

        This is a 'lossy' transaction where the slippage fee is realized
        by flooring the final credit.

        Returns
        -------
            A tuple of (ticker, pnl_precise, total_credit_precise, final_credit_int, invested_dollars_closed, is_partial_close)

        """
        async with self.database.get_conn() as conn:
            try:
                # 1. Get the position
                cursor = await conn.execute(
                    """
                    SELECT ticker, invested_dollars, entry_price
                    FROM positions
                    WHERE position_id = ? AND user_id = ? AND guild_id = ?
                    """,
                    (position_id, user_id, guild_id),
                )
                row = await cursor.fetchone()

                if not row:
                    msg = f"Position ID {position_id} not found or does not belong to you."
                    raise PortfolioNotFoundError(msg)

                ticker, invested_dollars_total, entry_price = row
                ticker = str(ticker)
                invested_dollars_total = int(invested_dollars_total)
                entry_price = float(entry_price)

                # 2. Determine Amount to Close
                invested_dollars_to_close = invested_dollars_total

                # A partial close is only possible if a close_amount is specified
                # and it's less than the total invested amount.
                is_partial_close = close_amount is not None and close_amount < abs(invested_dollars_total)

                # This explicit check helps the type checker narrow `close_amount` to `int`.
                # We know this is true because of the `is_partial_close` condition.
                if is_partial_close and close_amount is not None:
                    # We've already confirmed close_amount is not None, so this is safe.
                    # Match the sign of the original investment for the partial amount.
                    invested_dollars_to_close = close_amount if invested_dollars_total > 0 else -close_amount

                # 2. Get Current Price
                price_map = await self.price_cache.get_fresh_prices()
                current_price = price_map.get(ticker)
                if current_price is None or float(current_price) <= 0:
                    msg = f"Could not close position: Price for {ticker} is currently unavailable."
                    raise PriceNotAvailableError(msg)

                current_price_float = float(current_price)

                # 3. Calculate P&L and Credit
                # This simple math works for both long (invested_dollars > 0)
                # and short (invested_dollars < 0)
                price_change_pct = (current_price_float / entry_price) - 1.0
                pnl_precise = invested_dollars_to_close * price_change_pct

                # The total value to return to the user
                # For Long: 100 (principal) + 16.66 (pnl) = 116.66
                # For Short: 100 (abs(principal)) + 16.66 (pnl) = 116.66
                total_credit_precise = abs(invested_dollars_to_close) + pnl_precise

                # 4. Apply Slippage (The Money Sink)
                # We floor the credit. e.g., 116.66 -> 116.
                final_credit_int = math.floor(total_credit_precise)

                # 5. Update Bank Account
                await self._update_cash_balance(
                    conn,
                    user_id,
                    guild_id,
                    final_credit_int,  # This is the final credit
                )

                # 6. Update or Delete the position
                if is_partial_close:
                    new_invested_dollars = invested_dollars_total - invested_dollars_to_close
                    await conn.execute(
                        "UPDATE positions SET invested_dollars = ? WHERE position_id = ?",
                        (new_invested_dollars, position_id),
                    )
                else:
                    await conn.execute(
                        "DELETE FROM positions WHERE position_id = ?",
                        (position_id,),
                    )

                # 7. Commit
                await conn.commit()

                log.info(
                    "Position %s closed for user %s. P&L: %s",
                    position_id,
                    user_id,
                    pnl_precise,
                )

            except Exception:
                await conn.rollback()
                log.exception(
                    "Position close failed and was rolled back for user %s",
                    user_id,
                )
                raise

            else:
                return (
                    ticker,
                    pnl_precise,
                    total_credit_precise,
                    final_credit_int,
                    invested_dollars_to_close,
                    is_partial_close,
                )

    # --- Other Business Logic (e.g., portfolio P&L calculation) ---
    def is_market_open(self) -> bool:
        """Pass-through check to see if the US market is currently open based on the last API check."""
        # This now correctly checks the *cached* API state
        return self.price_cache.is_us_market_open()

    async def calculate_portfolio_value(
        self,
        user_id: UserId,
        guild_id: GuildId,
    ) -> dict | None:
        """Calculate the current cached market value and P&L of a user's portfolio."""
        await self.price_cache.get_fresh_prices()

        # 1. Fetch cash from UserDB
        user_cash_int = await self.user_db.get_stat(
            user_id,
            guild_id,
            StatName.CURRENCY,
        )
        user_cash_balance = float(user_cash_int)  # Use float for calculations

        # 2. Fetch all position lots
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                """
                SELECT position_id, ticker, invested_dollars, entry_price, timestamp
                FROM positions
                WHERE user_id = ? AND guild_id = ?
                ORDER BY timestamp ASC
                """,
                (user_id, guild_id),
            )
            rows = await cursor.fetchall()

        total_long_value = 0.0
        total_short_liability = 0.0
        total_short_collateral = 0.0
        total_pnl = 0.0
        positions_list = []

        for row in rows:
            pos_id, ticker, invested_dollars, entry_price, timestamp = row
            invested_dollars = int(invested_dollars)
            entry_price = float(entry_price)

            current_price, _ = self.price_cache.get_cached_price(ticker)

            pos_data = {
                "id": pos_id,
                "ticker": ticker,
                "invested": invested_dollars,  # e.g., 100 or -100
                "entry": entry_price,
                "timestamp": timestamp,
                "current_price": current_price,
                "current_value": None,
                "pnl": None,
            }

            if current_price is not None:
                current_price_float = float(current_price)
                price_change_pct = (current_price_float / entry_price) - 1.0
                pnl = invested_dollars * price_change_pct
                current_value = invested_dollars + pnl  # This is the "market value"

                pos_data["current_price"] = current_price_float
                pos_data["current_value"] = current_value
                pos_data["pnl"] = pnl

                total_pnl += pnl
                if invested_dollars > 0:
                    total_long_value += current_value
                else:
                    # current_value will be negative, e.g., -80
                    # This represents a liability of 80
                    total_short_liability += abs(current_value)
                    # Add the original investment amount to the collateral tracker
                    total_short_collateral += abs(invested_dollars)

            positions_list.append(pos_data)

        # Net Equity = Cash + (Value of Longs) - (Liability of Shorts) + (Collateral for Shorts)
        total_holdings_value = total_long_value - total_short_liability
        total_portfolio_value = user_cash_balance + total_holdings_value + total_short_collateral

        return {
            "cash_balance": user_cash_balance,
            "holdings_value": total_long_value,  # Value of long positions
            "short_liability": total_short_liability,  # ABS value of short positions
            "short_collateral": total_short_collateral,  # Collateral held for shorts
            "total_value": total_portfolio_value,  # True Net Equity
            "total_pnl": total_pnl,
            "positions": positions_list,  # List of dictionaries
        }
