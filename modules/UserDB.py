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
                    discord_id INTEGER PRIMARY KEY NOT NULL,
                    last_active_timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                    daily_reminder_preference TEXT NOT NULL DEFAULT 'NEVER',
                    has_claimed_daily INTEGER NOT NULL DEFAULT 0
                )
                """,
            )
            await conn.commit()

    async def update_last_message(self, discord_id: int) -> None:
        """Update the timestamp of the last message for a user."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id)
                VALUES (?)
                ON CONFLICT(discord_id) DO UPDATE SET
                last_active_timestamp = strftime('%Y-%m-%d %H:%M:%f', 'now')
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
                INSERT INTO {self.USERS_TABLE} (discord_id, daily_reminder_preference)
                VALUES (?, ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                daily_reminder_preference = excluded.daily_reminder_preference,
                last_active_timestamp = excluded.last_active_timestamp
                """,  # noqa: S608
                (discord_id, preference),
            )
            await conn.commit()

    async def get_active_users(self, days: int) -> list[int]:
        """Get a list of user IDs that have been active within a specified number of days."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT discord_id FROM {self.USERS_TABLE}
                WHERE julianday('now') - julianday(last_active_timestamp) <= ?
                """,  # noqa: S608
                (days,),
            )
            active_users = await cursor.fetchall()
        return [int(row[0]) for row in active_users]

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

    async def update_active_users(self, user_ids: list[int]) -> None:
        """Bulk update the last active timestamp for a list of users."""
        if not user_ids:
            return

        async with self.database.get_conn() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {self.USERS_TABLE} (discord_id) VALUES (?)
                ON CONFLICT(discord_id) DO UPDATE SET
                    last_active_timestamp = strftime('%Y-%m-%d %H:%M:%f', 'now')
                """,  # noqa: S608
                [(user_id,) for user_id in user_ids],
            )
            await conn.commit()

    async def attempt_daily_claim(self, discord_id: int) -> bool:
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
                INSERT INTO {self.USERS_TABLE} (discord_id, has_claimed_daily) VALUES (?, 1)
                ON CONFLICT(discord_id) DO UPDATE SET
                    has_claimed_daily = 1
                WHERE excluded.discord_id = ? AND {self.USERS_TABLE}.has_claimed_daily = 0
                """,  # noqa: S608
                (discord_id, discord_id),
            )
            await conn.commit()
            return cursor.rowcount == 1

    async def process_daily_reset(self) -> list[int]:
        """Atomically reset all daily claims and fetch users who need a reminder.

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
                WHERE daily_reminder_preference IN ('ALWAYS', 'ONCE')
                """,  # noqa: S608
            )
            user_ids_to_remind = [int(row[0]) for row in await cursor.fetchall()]

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
