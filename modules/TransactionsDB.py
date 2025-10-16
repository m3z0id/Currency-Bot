"""Manages the audit log for currency transactions.

This module provides the `transactions` table, which serves as an immutable
log of all currency movements between users. This ensures that every transfer
is auditable and traceable. The schema includes foreign keys to the `users`
table to maintain relational integrity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from modules.Database import Database


class TransactionsDB:
    """Manages the `transactions` audit log table."""

    TRANSACTIONS_TABLE: ClassVar[str] = "transactions"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def post_init(self) -> None:
        """Initialize the database table for transactions."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TRANSACTIONS_TABLE} (
                    transaction_id    INTEGER PRIMARY KEY,
                    guild_id          INTEGER NOT NULL CHECK(guild_id > 1000000),
                    sender_id         INTEGER NOT NULL CHECK(sender_id > 1000000),
                    receiver_id       INTEGER NOT NULL CHECK(receiver_id > 1000000),
                    stat_name         TEXT NOT NULL CHECK(stat_name IN ('currency')),
                    amount            INTEGER NOT NULL CHECK(amount > 0),
                    timestamp         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                    FOREIGN KEY (sender_id, guild_id) REFERENCES users(discord_id, guild_id),
                    FOREIGN KEY (receiver_id, guild_id) REFERENCES users(discord_id, guild_id),
                    CHECK(sender_id <> receiver_id)
                ) STRICT;
                """,
            )
            await conn.commit()
