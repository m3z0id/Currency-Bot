"""Manages user-specific data and preferences in the database.

This module handles the `users` table, which is the single source of truth for
all user-specific data, including stats (currency, bumps, XP), preferences
(reminders, opt-outs), and state (daily claims, activity). The schema is defined
with strict constraints and generated columns to enforce business logic and data
integrity directly at the database level.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, override

from modules.enums import StatName
from modules.types import GuildId, NonNegativeInt, PositiveInt, ReminderPreference, UserGuildPair, UserId

if TYPE_CHECKING:
    from modules.Database import Database
    from modules.TransactionsDB import TransactionsDB


# False S608: CURRENCY_TABLE is a constant, not user input. And stat.value is enum.
class UserDB:
    USERS_TABLE: ClassVar[str] = "users"

    def __init__(self, database: Database) -> None:
        self.database = database
        self.log = logging.getLogger(__name__)

    @override
    async def post_init(self) -> None:
        """Initialize the database table for users."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.USERS_TABLE} (
                    -- Core Identity
                    discord_id                  INTEGER NOT NULL CHECK(discord_id > 1000000),
                    guild_id                    INTEGER NOT NULL CHECK(guild_id > 1000000),

                    -- Stats (from former user_stats table)
                    currency                    INTEGER NOT NULL DEFAULT 0 CHECK(currency >= 0),
                    bumps                       INTEGER NOT NULL DEFAULT 0 CHECK(bumps >= 0),
                    xp                          INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),

                    -- Generated Level Column
                    level                       INTEGER GENERATED ALWAYS AS (CAST(floor(pow(max(xp - 6, 0), 1.0/2.5)) AS INTEGER))
                    STORED,

                    -- Preferences & State
                    last_active_timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                    daily_reminder_preference   TEXT NOT NULL DEFAULT 'NEVER'
                    CHECK(daily_reminder_preference IN ('NEVER', 'ONCE', 'ALWAYS')),

                    has_claimed_daily           INTEGER NOT NULL DEFAULT 0 CHECK(has_claimed_daily IN (0, 1)),
                    leveling_opt_out            INTEGER NOT NULL DEFAULT 0 CHECK(leveling_opt_out IN (0, 1)),

                    -- Keys & Constraints
                    PRIMARY KEY (discord_id, guild_id),

                    -- Invariant: 'ONCE' preference is only for users who haven't claimed.
                    CHECK(NOT (daily_reminder_preference = 'ONCE' AND has_claimed_daily = 1))
                ) STRICT;
                """,
            )
            # Add an index for guild_id and last_active_timestamp to speed up activity queries.
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_users_activity ON {self.USERS_TABLE}(guild_id, last_active_timestamp);
                """,
            )
            # Invariant: Bumps are append-only.
            await conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS prevent_bump_decrement
                BEFORE UPDATE ON {self.USERS_TABLE}
                WHEN NEW.bumps < OLD.bumps
                BEGIN
                    SELECT RAISE(ABORT, 'Bump count cannot be decreased');
                END;
                """,
            )
            # Create a view for user stats to abstract the underlying table.
            await conn.execute(
                """
                CREATE VIEW IF NOT EXISTS v_user_stats AS
                SELECT
                    discord_id,
                    guild_id,
                    currency,
                    bumps,
                    xp,
                    level,
                    leveling_opt_out
                FROM users;
                """,
            )
            await conn.commit()

    async def update_last_message(self, user_id: UserId, guild_id: GuildId) -> None:
        """Update the timestamp of the last message for a user."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, guild_id)
                VALUES (?, ?)
                ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                last_active_timestamp = strftime('%Y-%m-%d %H:%M:%S', 'now')
                """,  # noqa: S608
                (user_id, guild_id),
            )
            await conn.commit()

    async def set_daily_reminder_preference(
        self,
        user_id: UserId,
        preference: ReminderPreference,
        guild_id: GuildId,
    ) -> None:
        """Set the daily reminder preference ('ONCE', 'ALWAYS', 'NEVER') for a user."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, guild_id, daily_reminder_preference)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                daily_reminder_preference = excluded.daily_reminder_preference,
                last_active_timestamp = excluded.last_active_timestamp
                """,  # noqa: S608
                (user_id, guild_id, preference),
            )
            await conn.commit()

    async def set_leveling_opt_out(self, user_id: UserId, guild_id: GuildId, is_opted_out: bool) -> None:
        """Set the leveling opt-out preference for a user."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, guild_id, leveling_opt_out) VALUES (?, ?, ?)
                ON CONFLICT(discord_id, guild_id) DO UPDATE SET leveling_opt_out = excluded.leveling_opt_out
                """,  # noqa: S608
                (user_id, guild_id, 1 if is_opted_out else 0),
            )
            await conn.commit()

    async def is_user_opted_out(self, user_id: UserId, guild_id: GuildId) -> bool:
        """Check if a user has opted out of the leveling system."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT leveling_opt_out FROM {self.USERS_TABLE}
                WHERE discord_id = ? AND guild_id = ?
                """,  # noqa: S608
                (user_id, guild_id),
            )
            result = await cursor.fetchone()
        if result:
            # result[0] will be 1 if opted out, 0 otherwise
            return result[0] == 1
        # Default to not opted out if the user isn't in the table yet
        return False

    async def get_active_users(self, guild_id: GuildId, days: int) -> list[UserId]:
        """Get a list of user IDs that have been active within a specified number of days."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT discord_id FROM {self.USERS_TABLE}
                WHERE guild_id = ? AND julianday('now') - julianday(last_active_timestamp) <= ?
                """,  # noqa: S608
                (guild_id, days),
            )
            active_users = await cursor.fetchall()
        return [UserId(row[0]) for row in active_users]

    async def get_inactive_users(self, guild_id: GuildId, days: int) -> list[UserId]:
        """Get a list of user IDs that have been inactive for more than a specified number of days."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT discord_id FROM {self.USERS_TABLE}
                WHERE guild_id = ? AND julianday('now') - julianday(last_active_timestamp) > ?
                """,  # noqa: S608
                (guild_id, days),
            )
            inactive_users = await cursor.fetchall()
        return [UserId(row[0]) for row in inactive_users]

    async def update_active_users(self, user_guild_pairs: list[UserGuildPair]) -> None:
        """Bulk update the last active timestamp for a list of users."""
        if not user_guild_pairs:
            return

        async with self.database.get_conn() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, guild_id) VALUES (?, ?)
                ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                    last_active_timestamp = strftime('%Y-%m-%d %H:%M:%S', 'now')
                """,  # noqa: S608
                user_guild_pairs,
            )
            await conn.commit()

    async def attempt_daily_claim(self, user_id: UserId, guild_id: GuildId) -> bool:
        """Atomically attempt to claim a daily reward for a user.

        This method ensures a user is in the database and then tries to update
        their `has_claimed_daily` status from 0 to 1. This is done in a single
        transaction to prevent race conditions.

        Returns
        -------
            bool: True if the claim was successful, False if already claimed.

        """
        async with self.database.get_conn() as conn:
            # This single atomic operation ensures the user exists, then attempts
            # to update the claim status only if `has_claimed_daily` is 0.
            cursor = await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, guild_id, has_claimed_daily) VALUES (?, ?, 1)
                ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                    has_claimed_daily = 1
                WHERE {self.USERS_TABLE}.has_claimed_daily = 0
                """,  # noqa: S608
                (user_id, guild_id),
            )
            await conn.commit()
            return cursor.rowcount == 1

    async def process_daily_reset_for_guild(self, guild_id: GuildId) -> list[UserId]:
        """Atomically reset all daily claims and fetch users who need a reminder.

        This is currently dead code but remains separate to leave room for different timezones per server.

        This single transaction performs three actions:
        1. Fetches all users who have not claimed their daily and have reminders enabled.
        2. Resets `has_claimed_daily` to 0 for ALL users.
        3. Resets `daily_reminder_preference` to 'NEVER' for users who had it set to 'ONCE'.

        Returns
        -------
            A list of user IDs to be reminded.

        """
        async with self.database.get_conn() as conn:
            # 1. Fetch users who need a reminder BEFORE resetting claims.
            cursor = await conn.execute(
                f"""
                SELECT discord_id FROM {self.USERS_TABLE}
                WHERE guild_id = ? AND daily_reminder_preference IN ('ALWAYS', 'ONCE')
                """,  # noqa: S608
                (guild_id,),
            )
            user_ids_to_remind = [UserId(row[0]) for row in await cursor.fetchall()]

            # 2. Atomically reset daily claims and 'ONCE' preferences for all users.
            await conn.execute(
                f"""
                UPDATE {self.USERS_TABLE} SET
                    has_claimed_daily = 0,
                    daily_reminder_preference = CASE
                        WHEN daily_reminder_preference = 'ONCE' THEN 'NEVER'
                        ELSE daily_reminder_preference END
                WHERE guild_id = ?
                """,  # noqa: S608
                (guild_id,),
            )
            # Create a partial index to optimize fetching users who need a daily reminder.
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_users_pending_reminders
                ON users(guild_id)
                WHERE daily_reminder_preference IN ('ALWAYS', 'ONCE');
                """,
            )
            await conn.commit()
            return user_ids_to_remind

    async def process_daily_reset_all(self) -> list[UserId]:
        """Atomically reset all daily claims across all guilds and fetch users who need a reminder.

        This single transaction performs three actions for the entire database:
        1. Fetches all users who have not claimed their daily and have reminders enabled.
        2. Resets `has_claimed_daily` to 0 for ALL users.
        3. Resets `daily_reminder_preference` to 'NEVER' for users who had it set to 'ONCE'.

        Returns
        -------
            A list of user IDs to be reminded.

        """
        async with self.database.get_conn() as conn:
            # 1. Fetch all users who need a reminder from any guild.
            cursor = await conn.execute(
                f"""
                SELECT DISTINCT discord_id FROM {self.USERS_TABLE}
                WHERE daily_reminder_preference IN ('ALWAYS', 'ONCE')
                """,  # noqa: S608
            )
            user_ids_to_remind = [UserId(row[0]) for row in await cursor.fetchall()]

            # 2. Atomically reset daily claims and 'ONCE' preferences for all users.
            await conn.execute(
                f"""
                UPDATE {self.USERS_TABLE} SET
                    has_claimed_daily = 0,
                    daily_reminder_preference = CASE
                        WHEN daily_reminder_preference = 'ONCE' THEN 'NEVER'
                        ELSE daily_reminder_preference END
                """,  # noqa: S608
            )
            await conn.commit()
            return user_ids_to_remind

    # --- Stat Methods (Migrated from StatsDB) ---

    async def get_stat(self, user_id: UserId, guild_id: GuildId, stat: StatName) -> NonNegativeInt:
        """Get a single stat for a user, returning 0 if they don't exist."""
        async with self.database.get_cursor() as cursor:
            # The stat name is from an enum, so it's safe to use in an f-string.
            await cursor.execute(
                f"SELECT {stat.value} FROM {self.USERS_TABLE} WHERE discord_id = ? AND guild_id = ?",  # noqa: S608
                (user_id, guild_id),
            )
            result = await cursor.fetchone()
        return NonNegativeInt(int(result[0])) if result else NonNegativeInt(0)

    async def increment_stat(self, user_id: UserId, guild_id: GuildId, stat: StatName, amount: PositiveInt) -> int:
        """Atomically increments a user's stat using a positive value and returns the new value."""
        # stat.value is 'currency', 'bumps', or 'xp' which we safely use to build the query
        sql = f"""
            INSERT INTO {self.USERS_TABLE} (discord_id, guild_id, {stat.value})
            VALUES (?, ?, ?)
            ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                {stat.value} = {stat.value} + excluded.{stat.value}
            RETURNING {stat.value}
        """  # noqa: S608
        async with self.database.get_conn() as conn:
            cursor = await conn.execute(sql, (user_id, guild_id, amount))
            new_value_row = await cursor.fetchone()
            await conn.commit()
        return int(new_value_row[0]) if new_value_row else 0

    async def decrement_stat(
        self,
        user_id: UserId,
        guild_id: GuildId,
        stat: StatName,
        amount: PositiveInt,
    ) -> int | None:
        """Atomically decrements a user's stat if they have sufficient value."""
        # This new method uses a safe UPDATE instead of an UPSERT
        sql = f"""
            UPDATE {self.USERS_TABLE}
            SET {stat.value} = {stat.value} - ?
            WHERE discord_id = ? AND guild_id = ? AND {stat.value} >= ?
            RETURNING {stat.value}
        """  # noqa: S608
        async with self.database.get_conn() as conn:
            cursor = await conn.execute(sql, (amount, user_id, guild_id, amount))
            new_value_row = await cursor.fetchone()
            await conn.commit()
        return int(new_value_row[0]) if new_value_row else None

    async def set_stat(self, user_id: UserId, guild_id: GuildId, stat: StatName, value: int) -> None:
        """Atomically sets a user's stat to a specific value."""
        sql = f"""
            INSERT INTO {self.USERS_TABLE} (discord_id, guild_id, {stat.value})
            VALUES (?, ?, ?)
            ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                {stat.value} = excluded.{stat.value}
        """  # noqa: S608
        async with self.database.get_conn() as conn:
            await conn.execute(sql, (user_id, guild_id, value))
            await conn.commit()

    async def transfer_currency(
        self,
        sender_id: UserId,
        receiver_id: UserId,
        guild_id: GuildId,
        amount: PositiveInt,
        transactions_db: TransactionsDB,
    ) -> bool:
        """Atomically transfers currency and logs the transaction."""
        async with self.database.get_conn() as conn:
            try:
                # 1. Check sender's balance and decrement in one atomic step
                cursor = await conn.execute(
                    f"""UPDATE {self.USERS_TABLE} SET currency = currency - ?
                    WHERE discord_id = ? AND guild_id = ? AND currency >= ?""",  # noqa: S608
                    (amount, sender_id, guild_id, amount),
                )
                if cursor.rowcount == 0:
                    return False  # Insufficient funds or user not found

                # 2. Increment receiver's balance (UPSERT to be safe)
                await conn.execute(
                    f"""
                    INSERT INTO {self.USERS_TABLE} (discord_id, guild_id, currency) VALUES (?, ?, ?)
                    ON CONFLICT(discord_id, guild_id) DO UPDATE SET currency = currency + excluded.currency
                    """,  # noqa: S608
                    (receiver_id, guild_id, amount),
                )

                # 3. Log the transaction to the new table
                await conn.execute(
                    f"""INSERT INTO {transactions_db.TRANSACTIONS_TABLE} (sender_id, receiver_id, guild_id, stat_name, amount)
                    VALUES (?, ?, ?, ?, ?)""",  # noqa: S608
                    (sender_id, receiver_id, guild_id, StatName.CURRENCY.value, amount),
                )

                await conn.commit()
            except Exception:
                await conn.rollback()
                self.log.exception(
                    "Currency transfer failed and was rolled back. From %s to %s, amount %d",
                    sender_id,
                    receiver_id,
                    amount,
                )
                return False
            else:
                return True

    async def get_leaderboard(
        self,
        guild_id: GuildId,
        stat: StatName,
        limit: int = 10,
    ) -> list[tuple[int, UserId, int]]:
        """Retrieve the top users by a stat."""
        query_stat = "xp" if stat == StatName.XP else stat.value
        where_clause = "WHERE guild_id = ? AND leveling_opt_out = 0" if stat == StatName.XP else "WHERE guild_id = ?"

        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT
                    RANK() OVER (ORDER BY {query_stat} DESC) as rank,
                    discord_id,
                    {query_stat}
                FROM v_user_stats
                {where_clause}
                LIMIT ?
                """,  # noqa: S608
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
            return [(row[0], UserId(row[1]), row[2]) for row in rows]

    async def get_level_and_xp(self, user_id: UserId, guild_id: GuildId) -> tuple[int, int] | None:
        """Fetch the level and XP for a user."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"SELECT level, xp FROM {self.USERS_TABLE} WHERE discord_id = ? AND guild_id = ?",  # noqa: S608
                (user_id, guild_id),
            )
            return await cursor.fetchone()
