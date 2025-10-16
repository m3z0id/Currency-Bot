# In modules/config.py
import logging
import os
from dataclasses import dataclass
from typing import Self

from .types import GuildId, UserId

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotConfig:
    """A frozen dataclass to hold all bot configuration values."""

    token: str
    disboard_bot_id: UserId
    # Special case for leveling system which may operate on a privileged guild
    guild_id: GuildId | None
    swl_guild_id: GuildId | None
    udp_port: int | None

    @classmethod
    def from_environment(cls) -> Self:
        """Load all configuration from environment variables."""

        def get_env_int(name: str, required: bool = True) -> int | None:
            """Safely get and convert an environment variable to an int."""
            value = os.getenv(name)
            if value:
                try:
                    return int(value)
                except ValueError:
                    log.exception("'%s' is not a valid integer. Check your .env file.", name)
                    if required:
                        raise
                    return None
            if required:
                msg = f"Required environment variable '{name}' is not set."
                raise KeyError(msg)
            return None

        token = os.getenv("TOKEN")
        if not token:
            msg = "Required environment variable 'TOKEN' is not set."
            raise KeyError(msg)

        return cls(
            token=token,
            disboard_bot_id=UserId(get_env_int("DISBOARD_BOT_ID")),
            # Optional guild features
            guild_id=(GuildId(val) if (val := get_env_int("UDP_GUILD_ID", required=False)) else None),
            swl_guild_id=(GuildId(val) if (val := get_env_int("SWL_GUILD_ID", required=False)) else None),
            udp_port=get_env_int("UDP_PORT", required=False),
        )
