from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from modules.Database import Database


class TaskDB:
    """Manages persistent scheduled tasks for recovery after a bot restart."""

    TASKS_TABLE: ClassVar[str] = "scheduled_tasks"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def post_init(self) -> None:
        """Initialize the database table for scheduled tasks."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TASKS_TABLE} (
                    task_type TEXT PRIMARY KEY,
                    due_timestamp INTEGER NOT NULL
                ) STRICT;
                """,
            )
            await conn.commit()

    async def schedule_task(self, task_type: str, due_timestamp: float) -> None:
        """Persist a task to the database for recovery purposes."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.TASKS_TABLE} (task_type, due_timestamp)
                VALUES (?, ?)
                ON CONFLICT(task_type) DO UPDATE SET due_timestamp = excluded.due_timestamp
                """,
                (task_type, int(due_timestamp)),
            )
            await conn.commit()

    async def remove_task(self, task_type: str) -> None:
        """Remove a task from the database, typically after it has been completed."""
        async with self.database.get_conn() as conn:
            await conn.execute(
                f"DELETE FROM {self.TASKS_TABLE} WHERE task_type = ?",  # noqa: S608
                (task_type,),
            )
            await conn.commit()

    async def get_pending_tasks(self) -> list[tuple[str, int]]:
        """Fetch all pending tasks from the database on startup."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(f"SELECT task_type, due_timestamp FROM {self.TASKS_TABLE}")  # noqa: S608
            return await cursor.fetchall()

    async def get_pending_task(self, task_type: str) -> tuple[str, int] | None:
        """Fetch a single pending task by its type."""
        async with self.database.get_cursor() as cursor:
            await cursor.execute(
                f"SELECT task_type, due_timestamp FROM {self.TASKS_TABLE} WHERE task_type = ?",  # noqa: S608
                (task_type,),
            )
            return await cursor.fetchone()
