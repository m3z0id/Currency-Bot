from typing import ClassVar

from modules.Database import Database


# False S608: CURRENCY_TABLE is a constant, not user input
class UserDB:
    USERS_TABLE: ClassVar[str] = "users"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def post_init(self) -> None:
        """Initialize the database table for users."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.USERS_TABLE} (
                    discord_id TEXT UNIQUE NOT NULL,
                    last_active_timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    daily_reminder_preference TEXT NOT NULL DEFAULT 'NEVER',
                    daily_cooldown_ends TEXT
                )
                """,
            )
            await conn.commit()

    async def update_last_message(self, discord_id: int) -> None:
        """Update the timestamp of the last message for a user."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, last_active_timestamp)
                VALUES (?, datetime('now'))
                ON CONFLICT(discord_id) DO UPDATE SET
                last_active_timestamp = datetime('now')
                """,  # noqa: S608
                (discord_id,),
            )
            await conn.commit()

    async def set_daily_reminder_preference(
        self,
        discord_id: int,
        preference: str,
    ) -> None:
        """Set the daily reminder preference ('ONCE', 'ALWAYS', 'NEVER') for a user."""
        # Ensure the preference is one of the allowed values to prevent injection
        if preference not in ("ONCE", "ALWAYS", "NEVER"):
            msg = "Invalid preference value"
            raise ValueError(msg)

        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, last_active_timestamp, daily_reminder_preference)
                VALUES (?, datetime('now'), ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                daily_reminder_preference = excluded.daily_reminder_preference,
                last_active_timestamp = excluded.last_active_timestamp
                """,  # noqa: S608
                (discord_id, preference),
            )
            await conn.commit()

    async def get_inactive_users(self, days: int) -> list[int]:
        """Get a list of user IDs that have been inactive for more than a specified number of days."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT discord_id FROM {self.USERS_TABLE}
                WHERE julianday('now') - julianday(last_active_timestamp) > ?
                """,  # noqa: S608
                (days,),
            )
            inactive_users = await cursor.fetchall()
        return [int(row[0]) for row in inactive_users]

    async def bulk_update_last_message(self, activity_cache: dict[int, str]) -> None:
        """Bulk update last message timestamps from the activity cache."""
        if not activity_cache:
            return

        async with self.database.get_conn() as conn:
            data = [(str(discord_id), timestamp) for discord_id, timestamp in activity_cache.items()]

            await conn.executemany(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, last_active_timestamp)
                VALUES (?, ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                last_active_timestamp = excluded.last_active_timestamp
                """,  # noqa: S608
                data,
            )
            await conn.commit()

    async def set_daily_cooldown(self, discord_id: int, cooldown_ends: str) -> None:
        """Set the daily cooldown end time for a user."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id, last_active_timestamp, daily_cooldown_ends)
                VALUES (?, datetime('now'), ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                daily_cooldown_ends = excluded.daily_cooldown_ends,
                last_active_timestamp = excluded.last_active_timestamp
                """,  # noqa: S608
                (discord_id, cooldown_ends),
            )
            await conn.commit()

    async def get_users_ready_for_reminder(self) -> list[tuple[int, str]]:
        """Get users who have reminders enabled and whose cooldown has expired.

        Returns a list of tuples containing (discord_id, preference).
        """
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT discord_id, daily_reminder_preference FROM {self.USERS_TABLE}
                WHERE daily_reminder_preference IN ('ONCE', 'ALWAYS')
                AND daily_cooldown_ends IS NOT NULL
                AND datetime(daily_cooldown_ends) <= datetime('now')
                """,  # noqa: S608
            )
            users = await cursor.fetchall()
        return [(int(row[0]), row[1]) for row in users]

    async def reset_one_time_reminder(self, discord_id: int) -> None:
        """Set a 'ONCE' reminder back to 'NEVER' after it has been sent."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                UPDATE {self.USERS_TABLE}
                SET daily_reminder_preference = 'NEVER'
                WHERE discord_id = ?
                """,  # noqa: S608
                (discord_id,),
            )
            await conn.commit()

    async def get_daily_cooldown(self, discord_id: int) -> str | None:
        """Get the daily cooldown end time for a user.

        Returns the cooldown end time as an ISO string, or None if no cooldown is set.
        """
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT daily_cooldown_ends FROM {self.USERS_TABLE}
                WHERE discord_id = ?
                """,  # noqa: S608
                (discord_id,),
            )
            result = await cursor.fetchone()
        return result[0] if result and result[0] else None
