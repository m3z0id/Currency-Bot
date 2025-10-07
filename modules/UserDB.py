from typing import ClassVar, override

from modules.Database import Database
from modules.types import GuildId, ReminderPreference, UserGuildPair, UserId


# False S608: CURRENCY_TABLE is a constant, not user input
class UserDB:
    USERS_TABLE: ClassVar[str] = "users"

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    async def post_init(self) -> None:
        """Initialize the database table for users."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.USERS_TABLE} (
                    discord_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    last_active_timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                    daily_reminder_preference TEXT NOT NULL DEFAULT 'NEVER',
                    has_claimed_daily INTEGER NOT NULL DEFAULT 0,
                    leveling_opt_out INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (discord_id, guild_id)
                )
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

    async def process_daily_reset(self, guild_id: GuildId) -> list[UserId]:
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
            await conn.commit()
            return user_ids_to_remind
