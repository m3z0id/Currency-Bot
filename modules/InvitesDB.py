import logging
import os
from typing import Any

import aiohttp

from modules.Database import Database
from modules.types import GuildId, InviterId, UserId

log = logging.getLogger(__name__)

# --- Configuration & Session ---
TOKEN = os.getenv("TOKEN")
API_HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "Kiwibot/InviteTracker (aiohttp, 1.0)",
}


class InvitesDB:
    """Manages all database and API interactions for invite tracking."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def post_init(self) -> None:
        """Initialize the database table for invites."""
        async with self.database.get_conn() as conn:
            # Use INSERT OR IGNORE to replicate MariaDB's INSERT IGNORE behavior
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invites (
                    invitee INTEGER NOT NULL,
                    inviter INTEGER NOT NULL,
                    server INTEGER NOT NULL,
                    time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                    PRIMARY KEY (invitee, server)
                );
                """,
            )
            await conn.commit()
            log.info("Initialized invites database table.")

    async def insert_invite(
        self,
        invitee_id: UserId,
        inviter_id: InviterId,
        guild_id: GuildId,
        joined_at: str | None = None,
    ) -> bool:
        """Insert a new invite record.

        Returns True if a new row was added, False otherwise.
        """
        async with self.database.get_conn() as conn:
            if joined_at:
                # If a join time is provided, use it
                sql = "INSERT OR IGNORE INTO invites (invitee, inviter, server, time) VALUES (?, ?, ?, ?)"
                params = (invitee_id, inviter_id, guild_id, joined_at)
            else:
                # Otherwise, let the database use the default current time
                sql = "INSERT OR IGNORE INTO invites (invitee, inviter, server) VALUES (?, ?, ?)"
                params = (invitee_id, inviter_id, guild_id)

            cursor = await conn.execute(sql, params)
            await conn.commit()
            return cursor.rowcount == 1

    async def get_all_invitee_ids(self, guild_id: GuildId) -> set[UserId]:
        """Retrieve a set of all user IDs that have been invited in a guild."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute("SELECT DISTINCT invitee FROM invites WHERE server = ?", (guild_id,))
            rows = await cursor.fetchall()
            return {UserId(row[0]) for row in rows}

    async def get_invites_by_inviter(self, guild_id: GuildId) -> dict[InviterId, list[UserId]]:
        """Retrieve a dictionary mapping each inviter to a list of their invitees' IDs."""
        query = """
            SELECT inviter, GROUP_CONCAT(invitee)
            FROM invites
            WHERE server = ?
            GROUP BY inviter
        """
        result: dict[InviterId, list[UserId]] = {}
        async with self.database.get_cursor() as cursor:
            await cursor.execute(query, (guild_id,))
            rows = await cursor.fetchall()
            for inviter, invitees_str in rows:
                if invitees_str:
                    inviter_id: InviterId = int(inviter)
                    typed_invitees = [UserId(int(i)) for i in invitees_str.split(",")]
                    result[UserId(inviter_id) if inviter_id != 0 else 0] = typed_invitees
        return result

    # --- Discord Raw API Operations ---

    async def get_member_details_api(self, username: str, guild_id: int) -> dict[str, Any] | None:
        """Find a specific member by username and returns the one that joined most recently."""
        api_url = f"https://discord.com/api/v10/guilds/{guild_id}/members-search"
        payload = {"query": username, "limit": 5}

        try:
            async with aiohttp.ClientSession(headers=API_HEADERS) as session, session.get(api_url, params=payload) as resp:
                resp.raise_for_status()
                members = await resp.json()
                if not members:
                    return None
                # Return the member with the most recent join date
                return max(members, key=lambda m: m.get("joined_at", ""))
        except aiohttp.ClientError:
            log.exception("API request failed for user %s", username)
            return None

    async def get_all_guild_members_api(self, guild_id: int) -> list[dict[str, Any]]:
        """Fetch all members from a guild using the members-search endpoint."""
        api_url = f"https://discord.com/api/v10/guilds/{guild_id}/members-search"
        # This endpoint uses POST with an empty query to return all members.
        payload = {"limit": 1000}
        try:
            async with (
                aiohttp.ClientSession(headers=API_HEADERS) as session,
                session.post(api_url, json=payload) as resp,
            ):
                resp.raise_for_status()
                data = await resp.json()
                return data.get("members", [])
        except aiohttp.ClientError:
            log.exception("API request to fetch all guild members failed")
            return []
