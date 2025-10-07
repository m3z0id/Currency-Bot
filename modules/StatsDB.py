from typing import ClassVar

from modules.Database import Database
from modules.enums import StatName


class StatsDB:
    STATS_TABLE: ClassVar[str] = "user_stats"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def post_init(self) -> None:
        async with self.database.get_conn() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_stats (
                    discord_id INTEGER NOT NULL,
                    stat_name TEXT NOT NULL CHECK(stat_name IN ('currency', 'bumps', 'xp')),
                    value INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (discord_id, stat_name)
                );
                """,
            )

            await conn.commit()

    async def get_stat(self, discord_id: int, stat: StatName) -> int:
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                "SELECT value FROM user_stats WHERE discord_id = ? AND stat_name = ?",
                (discord_id, stat.value),
            )
            result = await cursor.fetchone()
        return int(result[0]) if result else 0

    async def increment_stat(self, discord_id: int, stat: StatName, amount: int) -> int:
        """Atomically increments a user's stat and returns the new value."""
        async with self.database.get_conn() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO user_stats (discord_id, stat_name, value)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_id, stat_name) DO UPDATE SET
                value = value + excluded.value
                RETURNING value
                """,
                (discord_id, stat.value, amount),
            )
            # Fetch the new value that was returned by the query
            new_value_row = await cursor.fetchone()
            await conn.commit()

            # new_value_row[0] contains the user's new total value
            return int(new_value_row[0]) if new_value_row else amount

    async def set_stat(self, discord_id: int, stat: StatName, amount: int) -> None:
        """Set a user's stat to a specific value."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO user_stats (discord_id, stat_name, value)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_id, stat_name) DO UPDATE SET
                value = excluded.value
                """,
                (discord_id, stat.value, amount),
            )
            await conn.commit()

    async def transfer_stat(self, sender_id: int, receiver_id: int, stat: StatName, amount: int) -> bool:
        """Atomically transfers a specified amount from a sender to a receiver.

        This entire operation is performed within a single database transaction.
        If any step fails (e.g., insufficient funds), the entire transaction
        is rolled back.

        """
        async with self.database.get_conn() as conn:
            # Check sender balance
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT value FROM user_stats WHERE discord_id = ? AND stat_name = ?",
                (sender_id, stat.value),
            )
            balance_row = await cursor.fetchone()
            sender_balance = int(balance_row[0]) if balance_row else 0

            if sender_balance < amount:
                return False

            # Decrement sender's stat
            await conn.execute(
                "UPDATE user_stats SET value = value - ? WHERE discord_id = ? AND stat_name = ?",
                (amount, sender_id, stat.value),
            )

            # Increment receiver's stat
            await conn.execute(
                """
                INSERT INTO user_stats (discord_id, stat_name, value) VALUES (?, ?, ?)
                ON CONFLICT(discord_id, stat_name) DO UPDATE SET value = value + excluded.value
                """,
                (receiver_id, stat.value, amount),
            )

            await conn.commit()
            return True

    async def get_leaderboard(self, stat: StatName, limit: int = 10) -> list[tuple[int, int]]:
        """Retrieve the top users by a stat, filtering out users who have opted-out."""
        async with self.database.get_cursor() as cursor:
            # If the stat is not XP, we don't need to filter for opt-outs.
            if stat != StatName.XP:
                await cursor.execute(
                    "SELECT discord_id, value FROM user_stats WHERE stat_name = ? ORDER BY value DESC LIMIT ?",
                    (stat.value, limit),
                )
                return await cursor.fetchall()

            # For XP, we perform a JOIN to filter out opted-out users efficiently.
            await cursor.execute(
                """
                SELECT s.discord_id, s.value
                FROM user_stats s
                LEFT JOIN users u ON s.discord_id = u.discord_id
                WHERE s.stat_name = ? AND (u.leveling_opt_out IS NULL OR u.leveling_opt_out = 0)
                ORDER BY s.value DESC
                LIMIT ?
                """,
                (stat.value, limit),
            )
            return await cursor.fetchall()
